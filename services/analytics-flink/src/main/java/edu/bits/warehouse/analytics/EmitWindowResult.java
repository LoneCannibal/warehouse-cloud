package edu.bits.warehouse.analytics;

import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.streaming.api.functions.windowing.ProcessWindowFunction;
import org.apache.flink.streaming.api.windowing.windows.TimeWindow;
import org.apache.flink.util.Collector;

/**
 * Turns a finished window into a result.
 *
 * <p>Queue depth is not a per-window count: it carries over from one window to the
 * next. So it lives in keyed state that outlives each window, which Flink checkpoints
 * to Cloud Storage and restores after a failure. Every window of a zone adds its net change.
 */
public class EmitWindowResult
        extends ProcessWindowFunction<WindowAccumulator, WindowResult, String, TimeWindow> {

    private static final ValueStateDescriptor<Long> QUEUE_DEPTH =
            new ValueStateDescriptor<>("queue-depth", Long.class);

    @Override
    public void process(
            String zone,
            Context context,
            Iterable<WindowAccumulator> elements,
            Collector<WindowResult> out)
            throws Exception {
        WindowAccumulator totals = elements.iterator().next();

        ValueState<Long> depthState = context.globalState().getState(QUEUE_DEPTH);
        long previous = depthState.value() == null ? 0L : depthState.value();
        long entered = totals.tasksCreated + totals.tasksAbandoned;
        long depth = Math.max(0L, previous + entered - totals.tasksAssigned);
        depthState.update(depth);

        WindowResult result = new WindowResult();
        result.zone = zone;
        result.windowStart = context.window().getStart();
        result.windowEnd = context.window().getEnd();
        result.tasksCreated = totals.tasksCreated;
        result.picksCompleted = totals.picksCompleted;
        result.packsCompleted = totals.packsCompleted;
        result.dispatched = totals.dispatched;
        result.tasksAbandoned = totals.tasksAbandoned;
        result.tasksReclaimed = totals.tasksReclaimed;
        result.activeWorkers = totals.workers.size();
        result.queueDepth = depth;
        result.meanRecoveryMs =
                totals.tasksReclaimed == 0 ? 0.0 : (double) totals.recoveryMsTotal / totals.tasksReclaimed;
        out.collect(result);
    }
}
