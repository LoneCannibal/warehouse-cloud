package edu.bits.warehouse.analytics;

import java.time.Duration;
import java.util.Properties;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.utils.ParameterTool;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.datastream.SingleOutputStreamOperator;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.windowing.assigners.TumblingEventTimeWindows;
import org.apache.flink.streaming.api.windowing.time.Time;
import org.apache.flink.util.OutputTag;
import org.apache.kafka.clients.consumer.OffsetResetStrategy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Warehouse Analytics: stateful, time-windowed aggregation of task events.
 *
 * <p>Reads warehouse.task-events, computes per-zone throughput, queue depth and
 * abandoned-task recovery over one-minute event-time windows, and writes each result
 * to warehouse.analytics.results.
 */
public final class WarehouseAnalyticsJob {
    private static final Logger LOG = LoggerFactory.getLogger(WarehouseAnalyticsJob.class);

    static final OutputTag<TaskEvent> LATE_EVENTS = new OutputTag<TaskEvent>("late-events") {};

    static final String GOOGLE_LOGIN_HANDLER = "com.google.cloud.hosted.kafka.auth.GcpLoginCallbackHandler";

    private WarehouseAnalyticsJob() {}

    public static void main(String[] args) throws Exception {
        ParameterTool params = ParameterTool.fromArgs(args);
        String bootstrap = params.getRequired("bootstrap");
        String sourceTopic = params.get("source-topic", "warehouse.task-events");
        String resultsTopic = params.get("results-topic", "warehouse.analytics.results");
        Duration window = Duration.ofSeconds(params.getLong("window-seconds", 60));
        // Longer than a Job Scheduler failover (about 11 s), because outbox events wait for
        // the new leader and would otherwise reach Flink behind the watermark and be dropped.
        Duration maxLateness = Duration.ofSeconds(params.getLong("watermark-seconds", 20));

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(params.getLong("checkpoint-ms", 30_000));
        if (params.has("checkpoint-dir")) {
            env.getCheckpointConfig().setCheckpointStorage(params.get("checkpoint-dir"));
        }

        Properties kafka = kafkaProperties(params);

        KafkaSource<TaskEvent> source = KafkaSource.<TaskEvent>builder()
                .setBootstrapServers(bootstrap)
                .setTopics(sourceTopic)
                .setGroupId("warehouse-analytics-flink")
                .setStartingOffsets(OffsetsInitializer.committedOffsets(OffsetResetStrategy.EARLIEST))
                .setValueOnlyDeserializer(new TaskEventSchema())
                .setProperties(kafka)
                .build();

        KafkaSink<String> sink = KafkaSink.<String>builder()
                .setBootstrapServers(bootstrap)
                .setKafkaProducerConfig(kafka)
                .setDeliveryGuarantee(DeliveryGuarantee.AT_LEAST_ONCE)
                .setRecordSerializer(KafkaRecordSerializationSchema.builder()
                        .setTopic(resultsTopic)
                        .setValueSerializationSchema(new SimpleStringSchema())
                        .build())
                .build();

        DataStream<TaskEvent> events = env.fromSource(source, watermarks(maxLateness), "task-events");

        SingleOutputStreamOperator<WindowResult> results = aggregate(events, window);

        results.getSideOutput(LATE_EVENTS)
                .map(event -> {
                    LOG.warn("late event dropped: {} {}", event.eventType, event.taskId);
                    return event.taskId;
                })
                .name("log-late-events");

        results.map(WindowResult::toJson).name("to-json").sinkTo(sink).name("analytics-results");

        env.execute("warehouse-analytics");
    }

    /**
     * Client settings for the broker. With {@code --kafka-auth google} the job logs in to
     * Managed Service for Apache Kafka as the Dataproc cluster's service account, using
     * Google's login handler. Otherwise it uses {@code --security-protocol}, for a local broker.
     */
    static Properties kafkaProperties(ParameterTool params) {
        Properties kafka = new Properties();
        if ("google".equals(params.get("kafka-auth", "none"))) {
            kafka.setProperty("security.protocol", "SASL_SSL");
            kafka.setProperty("sasl.mechanism", "OAUTHBEARER");
            kafka.setProperty("sasl.login.callback.handler.class", GOOGLE_LOGIN_HANDLER);
            kafka.setProperty("sasl.jaas.config",
                    "org.apache.kafka.common.security.oauthbearer.OAuthBearerLoginModule required;");
        } else {
            kafka.setProperty("security.protocol", params.get("security-protocol", "PLAINTEXT"));
        }
        return kafka;
    }

    /** Event time comes from the event. The watermark trails the newest one by maxLateness. */
    static WatermarkStrategy<TaskEvent> watermarks(Duration maxLateness) {
        return WatermarkStrategy.<TaskEvent>forBoundedOutOfOrderness(maxLateness)
                .withTimestampAssigner((event, kafkaTimestamp) -> event.ts)
                .withIdleness(Duration.ofSeconds(30));
    }

    /** The windowing core, separated from Kafka so that a test can drive it directly. */
    static SingleOutputStreamOperator<WindowResult> aggregate(DataStream<TaskEvent> events, Duration window) {
        return events
                .keyBy(event -> event.zone)
                // Flink 1.17 takes a Time here. The Duration overload arrived in 1.19.
                .window(TumblingEventTimeWindows.of(Time.milliseconds(window.toMillis())))
                .sideOutputLateData(LATE_EVENTS)
                .aggregate(new WindowAggregate(), new EmitWindowResult())
                .name("zone-window-aggregation");
    }
}
