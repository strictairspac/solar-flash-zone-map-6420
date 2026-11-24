export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
  building: "重建中", active: "生效中", abandoned: "已放弃",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败",
  review: "复核签署", correct_reading: "补录复测", abort_correction: "重建中断",
});
