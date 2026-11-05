package edu.bits.warehouse.analytics;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import java.io.IOException;

/**
 * One record from the warehouse.task-events topic. See contracts/task-event.schema.json.
 *
 * <p>Public fields and a no-argument constructor make this a Flink POJO, which Flink
 * serializes more efficiently than a generic Kryo type.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class TaskEvent {
    public static final String TASK_CREATED = "TASK_CREATED";
    public static final String TASK_ASSIGNED = "TASK_ASSIGNED";
    public static final String TASK_STARTED = "TASK_STARTED";
    public static final String TASK_COMPLETED = "TASK_COMPLETED";
    public static final String TASK_ABANDONED = "TASK_ABANDONED";
    public static final String TASK_RECLAIMED = "TASK_RECLAIMED";
    public static final String SHIPMENT_DISPATCHED = "SHIPMENT_DISPATCHED";

    private static final ObjectMapper MAPPER =
            new ObjectMapper().setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);

    public String eventId;
    public String eventType;
    public String taskId;
    public String orderId;
    public String taskType;
    public String zone;
    public String workerId;
    public int attempt;
    public Long recoveryMs;
    public long ts;

    public TaskEvent() {}

    public static TaskEvent parse(String json) throws IOException {
        return MAPPER.readValue(json, TaskEvent.class);
    }
}
