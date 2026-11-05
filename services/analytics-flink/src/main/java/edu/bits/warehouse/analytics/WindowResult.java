package edu.bits.warehouse.analytics;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;

/** One record on warehouse.analytics.results. See contracts/analytics-result.schema.json. */
public class WindowResult {
    private static final ObjectMapper MAPPER =
            new ObjectMapper().setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);

    public String zone;
    public long windowStart;
    public long windowEnd;
    public long tasksCreated;
    public long picksCompleted;
    public long packsCompleted;
    public long dispatched;
    public long tasksAbandoned;
    public long tasksReclaimed;
    public long activeWorkers;
    public long queueDepth;
    public double meanRecoveryMs;

    public WindowResult() {}

    public String toJson() throws JsonProcessingException {
        return MAPPER.writeValueAsString(this);
    }
}
