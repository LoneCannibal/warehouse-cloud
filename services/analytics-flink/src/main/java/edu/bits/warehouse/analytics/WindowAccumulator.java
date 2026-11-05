package edu.bits.warehouse.analytics;

import java.util.HashSet;

/** Running totals for one zone in one window. */
public class WindowAccumulator {
    public long tasksCreated;
    public long tasksAssigned;
    public long picksCompleted;
    public long packsCompleted;
    public long dispatched;
    public long tasksAbandoned;
    public long tasksReclaimed;
    public long recoveryMsTotal;
    public HashSet<String> workers = new HashSet<>();

    public WindowAccumulator() {}
}
