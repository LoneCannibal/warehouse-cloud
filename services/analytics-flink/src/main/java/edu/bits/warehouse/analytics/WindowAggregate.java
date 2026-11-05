package edu.bits.warehouse.analytics;

import org.apache.flink.api.common.functions.AggregateFunction;

/** Folds task events into one accumulator per zone per window. */
public class WindowAggregate implements AggregateFunction<TaskEvent, WindowAccumulator, WindowAccumulator> {

    @Override
    public WindowAccumulator createAccumulator() {
        return new WindowAccumulator();
    }

    @Override
    public WindowAccumulator add(TaskEvent event, WindowAccumulator totals) {
        if (event.workerId != null) {
            totals.workers.add(event.workerId);
        }
        switch (event.eventType) {
            case TaskEvent.TASK_CREATED:
                totals.tasksCreated++;
                break;
            case TaskEvent.TASK_ASSIGNED:
                totals.tasksAssigned++;
                break;
            case TaskEvent.TASK_COMPLETED:
                if ("PICK".equals(event.taskType)) {
                    totals.picksCompleted++;
                } else if ("PACK".equals(event.taskType)) {
                    totals.packsCompleted++;
                }
                break;
            case TaskEvent.TASK_ABANDONED:
                totals.tasksAbandoned++;
                break;
            case TaskEvent.TASK_RECLAIMED:
                totals.tasksReclaimed++;
                if (event.recoveryMs != null) {
                    totals.recoveryMsTotal += event.recoveryMs;
                }
                break;
            case TaskEvent.SHIPMENT_DISPATCHED:
                totals.dispatched++;
                break;
            default:
                break;
        }
        return totals;
    }

    @Override
    public WindowAccumulator getResult(WindowAccumulator totals) {
        return totals;
    }

    @Override
    public WindowAccumulator merge(WindowAccumulator a, WindowAccumulator b) {
        a.tasksCreated += b.tasksCreated;
        a.tasksAssigned += b.tasksAssigned;
        a.picksCompleted += b.picksCompleted;
        a.packsCompleted += b.packsCompleted;
        a.dispatched += b.dispatched;
        a.tasksAbandoned += b.tasksAbandoned;
        a.tasksReclaimed += b.tasksReclaimed;
        a.recoveryMsTotal += b.recoveryMsTotal;
        a.workers.addAll(b.workers);
        return a;
    }
}
