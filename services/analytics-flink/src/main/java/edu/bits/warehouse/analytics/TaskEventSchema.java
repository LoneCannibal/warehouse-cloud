package edu.bits.warehouse.analytics;

import org.apache.flink.api.common.serialization.AbstractDeserializationSchema;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Parses events inside the Kafka source.
 *
 * <p>Parsing here, rather than in a later operator, lets the source assign event time
 * from each event's own ts and track watermarks per partition. The Kafka record
 * timestamp would be wrong: the outbox relay can publish an event well after it happened.
 *
 * <p>Returning null skips the record, so one bad message cannot restart the job.
 */
public class TaskEventSchema extends AbstractDeserializationSchema<TaskEvent> {
    private static final Logger LOG = LoggerFactory.getLogger(TaskEventSchema.class);

    @Override
    public TaskEvent deserialize(byte[] message) {
        try {
            return TaskEvent.parse(new String(message, java.nio.charset.StandardCharsets.UTF_8));
        } catch (Exception error) {
            LOG.warn("dropping unparseable event: {}", error.getMessage());
            return null;
        }
    }
}
