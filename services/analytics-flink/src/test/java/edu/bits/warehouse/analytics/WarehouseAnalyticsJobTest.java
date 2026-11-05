package edu.bits.warehouse.analytics;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Properties;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.CloseableIterator;
import org.apache.kafka.common.security.auth.AuthenticateCallbackHandler;
import org.junit.jupiter.api.Test;

/** Runs the real window pipeline on Flink's embedded cluster. */
class WarehouseAnalyticsJobTest {
    private static final long MINUTE = 60_000L;

    private static TaskEvent event(String type, String taskType, String zone, String worker, long ts) {
        TaskEvent event = new TaskEvent();
        event.eventType = type;
        event.taskType = taskType;
        event.zone = zone;
        event.workerId = worker;
        event.ts = ts;
        return event;
    }

    private static TaskEvent reclaimed(String zone, String worker, long recoveryMs, long ts) {
        TaskEvent event = event(TaskEvent.TASK_RECLAIMED, "PICK", zone, worker, ts);
        event.recoveryMs = recoveryMs;
        return event;
    }

    private static List<WindowResult> run(TaskEvent... events) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);
        DataStream<TaskEvent> stream = env.fromCollection(Arrays.asList(events))
                .assignTimestampsAndWatermarks(WatermarkStrategy.<TaskEvent>forMonotonousTimestamps()
                        .withTimestampAssigner((event, previous) -> event.ts));

        List<WindowResult> results = new ArrayList<>();
        try (CloseableIterator<WindowResult> output =
                WarehouseAnalyticsJob.aggregate(stream, Duration.ofMinutes(1)).executeAndCollect()) {
            output.forEachRemaining(results::add);
        }
        results.sort(Comparator.comparing((WindowResult r) -> r.zone).thenComparing(r -> r.windowStart));
        return results;
    }

    @Test
    void countsEventsPerZonePerWindow() throws Exception {
        List<WindowResult> results = run(
                event(TaskEvent.TASK_COMPLETED, "PICK", "A", "w1", 1_000),
                event(TaskEvent.TASK_COMPLETED, "PICK", "A", "w2", 2_000),
                event(TaskEvent.TASK_COMPLETED, "PICK", "B", "w1", 3_000),
                event(TaskEvent.TASK_COMPLETED, "PICK", "A", "w1", MINUTE + 1_000));

        assertEquals(3, results.size());
        assertEquals("A", results.get(0).zone);
        assertEquals(0L, results.get(0).windowStart);
        assertEquals(MINUTE, results.get(0).windowEnd);
        assertEquals(2, results.get(0).picksCompleted);
        assertEquals(1, results.get(1).picksCompleted);
        assertEquals(MINUTE, results.get(1).windowStart);
        assertEquals("B", results.get(2).zone);
    }

    @Test
    void countsUniqueWorkersNotEvents() throws Exception {
        List<WindowResult> results = run(
                event(TaskEvent.TASK_STARTED, "PICK", "A", "w1", 1_000),
                event(TaskEvent.TASK_COMPLETED, "PICK", "A", "w1", 2_000),
                event(TaskEvent.TASK_STARTED, "PICK", "A", "w2", 3_000));

        assertEquals(2, results.get(0).activeWorkers);
    }

    @Test
    void queueDepthCarriesAcrossWindows() throws Exception {
        List<WindowResult> results = run(
                event(TaskEvent.TASK_CREATED, "PICK", "A", null, 1_000),
                event(TaskEvent.TASK_CREATED, "PICK", "A", null, 2_000),
                event(TaskEvent.TASK_CREATED, "PICK", "A", null, 3_000),
                event(TaskEvent.TASK_ASSIGNED, "PICK", "A", "w1", 4_000),
                event(TaskEvent.TASK_ASSIGNED, "PICK", "A", "w1", MINUTE + 1_000),
                event(TaskEvent.TASK_CREATED, "PICK", "A", null, 2 * MINUTE + 1_000));

        assertEquals(2, results.get(0).queueDepth);
        assertEquals(1, results.get(1).queueDepth);
        assertEquals(2, results.get(2).queueDepth);
    }

    @Test
    void abandonedTaskGoesBackInTheQueueAndRecoveryIsAveraged() throws Exception {
        List<WindowResult> results = run(
                event(TaskEvent.TASK_ABANDONED, "PICK", "A", "w1", 1_000),
                reclaimed("A", "w2", 1_000, 2_000),
                event(TaskEvent.TASK_ABANDONED, "PICK", "A", "w3", 3_000),
                reclaimed("A", "w2", 3_000, 4_000));

        WindowResult window = results.get(0);
        assertEquals(2, window.tasksAbandoned);
        assertEquals(2, window.tasksReclaimed);
        assertEquals(2_000.0, window.meanRecoveryMs);
        assertEquals(2, window.queueDepth);
    }

    @Test
    void separatesPickPackAndDispatchThroughput() throws Exception {
        List<WindowResult> results = run(
                event(TaskEvent.TASK_COMPLETED, "PACK", "P", "p1", 1_000),
                event(TaskEvent.SHIPMENT_DISPATCHED, "DISPATCH", "D", null, 2_000));

        assertEquals("D", results.get(0).zone);
        assertEquals(1, results.get(0).dispatched);
        assertEquals("P", results.get(1).zone);
        assertEquals(1, results.get(1).packsCompleted);
        assertEquals(0, results.get(1).picksCompleted);
    }

    @Test
    void parsesAnEventFromThePythonServices() throws Exception {
        TaskEvent event = TaskEvent.parse(
                "{\"event_id\":\"e1\",\"event_type\":\"TASK_RECLAIMED\",\"task_id\":\"t1\","
                        + "\"order_id\":\"o1\",\"task_type\":\"PICK\",\"zone\":\"A\",\"worker_id\":\"w1\","
                        + "\"attempt\":2,\"recovery_ms\":1500,\"ts\":1700000000000,\"future_field\":true}");

        assertEquals(TaskEvent.TASK_RECLAIMED, event.eventType);
        assertEquals(1500L, event.recoveryMs);
        assertEquals(1700000000000L, event.ts);
    }

    @Test
    void resultUsesTheSnakeCaseContract() throws Exception {
        WindowResult result = new WindowResult();
        result.zone = "A";
        result.windowStart = 60_000L;

        String json = result.toJson();

        assertEquals(true, json.contains("\"window_start\":60000"));
        assertEquals(true, json.contains("\"mean_recovery_ms\":0.0"));
    }

    @Test
    void managedKafkaLoginUsesGooglesHandlerAndTheHandlerIsPackaged() throws Exception {
        Properties kafka = WarehouseAnalyticsJob.kafkaProperties(
                ParameterTool.fromArgs(new String[] {"--kafka-auth", "google"}));

        assertEquals("SASL_SSL", kafka.getProperty("security.protocol"));
        assertEquals("OAUTHBEARER", kafka.getProperty("sasl.mechanism"));
        // Loading the class proves it links against the connector's kafka-clients.
        Class<?> handler = Class.forName(kafka.getProperty("sasl.login.callback.handler.class"));
        assertTrue(AuthenticateCallbackHandler.class.isAssignableFrom(handler));
    }

    @Test
    void aLocalBrokerKeepsThePlainProtocol() {
        Properties kafka = WarehouseAnalyticsJob.kafkaProperties(ParameterTool.fromArgs(new String[] {}));

        assertEquals("PLAINTEXT", kafka.getProperty("security.protocol"));
        assertEquals(null, kafka.getProperty("sasl.mechanism"));
    }
}
