"""Pure costing logic for a provisional, non-posting receipt preview.

An initial receipt consumes the order's unreceived quantity. A rework return
consumes the outstanding rework pool, so rework never consumes material twice.
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class CostingError(ValueError):
    pass


def number(value, field):
    try:
        result = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError) as exc:
        raise CostingError(f"{field} 必须是数字") from exc
    if not result.is_finite() or result < 0:
        raise CostingError(f"{field} 必须是非负有限数字")
    return result


def money(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def text_number(value):
    return format(value.normalize(), "f") if value else "0"


def receipt_batch_quantities(detail, returned_qty=0):
    accepted = number(detail.get("qty"), "收货接收数量")
    rejected = number(detail.get("rejected_qty"), "收货拒收数量")
    lost = number(detail.get("process_loss_qty"), "收货工艺损耗")
    returned = number(returned_qty, "已提交退货数量")
    original = accepted + rejected + lost
    if original <= 0 or returned > accepted + rejected:
        raise CostingError("收货或退货数量无效，退货不能超过实际接收和拒收数量")
    return {"qty": original - returned, "lost_qty": lost,
            "original_qty": original, "returned_qty": returned}


def calculate(planned_qty, material_amount, processing_amount, other_amount, batches):
    planned = number(planned_qty, "订单数量")
    if planned <= 0:
        raise CostingError("订单数量必须大于零")
    material = number(material_amount, "材料费")
    processing = number(processing_amount, "基础加工费")
    other = number(other_amount, "其他费用")

    initial_total = Decimal(0)
    good_total = Decimal(0)
    rework_open = Decimal(0)
    lost_total = Decimal(0)
    pending_total = Decimal(0)
    batch_cost_total = Decimal(0)
    parsed = []

    for index, raw in enumerate(batches, 1):
        kind = raw.get("receipt_type")
        if kind not in ("首次回货", "返工回货", "待检处理"):
            raise CostingError(f"第 {index} 行回货类型无效")
        qty = number(raw.get("received_qty"), f"第 {index} 行回货数量")
        good = number(raw.get("good_qty"), f"第 {index} 行合格数量")
        rework = number(raw.get("rework_qty"), f"第 {index} 行返工数量")
        lost = number(raw.get("lost_qty"), f"第 {index} 行损耗数量")
        pending = number(raw.get("pending_qty"), f"第 {index} 行待检数量")
        extra = number(raw.get("extra_processing_amount"), f"第 {index} 行追加加工费")
        if qty <= 0:
            raise CostingError(f"第 {index} 行回货数量必须大于零")
        if good + rework + lost + pending != qty:
            raise CostingError(f"第 {index} 行合格、返工、损耗、待检数量之和必须等于回货数量")

        if kind == "首次回货":
            initial_total += qty
            if initial_total > planned:
                raise CostingError("首次回货累计数量不能超过订单数量")
        elif kind == "返工回货":
            if qty > rework_open:
                raise CostingError(f"第 {index} 行返工回货超过待返工数量")
            rework_open -= qty
        else:
            if qty > pending_total:
                raise CostingError(f"第 {index} 行待检处理超过待检数量")
            pending_total -= qty

        rework_open += rework
        good_total += good
        lost_total += lost
        pending_total += pending
        batch_cost_total += extra
        parsed.append({"index": index, "receipt_type": kind, "good_qty": good, "extra_cost": extra})

    unreceived = planned - initial_total
    potential_good = planned - lost_total
    total_cost = material + processing + other + batch_cost_total
    if potential_good == 0:
        unit_cost = Decimal(0)
    else:
        unit_cost = total_cost / potential_good

    allocated = Decimal(0)
    cumulative_good = Decimal(0)
    allocations = []
    for batch in parsed:
        cumulative_good += batch["good_qty"]
        target = money(unit_cost * cumulative_good)
        amount = target - allocated
        allocated += amount
        allocations.append({
            "row": batch["index"],
            "good_qty": text_number(batch["good_qty"]),
            "estimated_cost": text_number(amount),
        })

    complete = unreceived == rework_open == pending_total == 0

    return {
        "planned_qty": text_number(planned),
        "initial_received_qty": text_number(initial_total),
        "good_qty": text_number(good_total),
        "rework_open_qty": text_number(rework_open),
        "lost_qty": text_number(lost_total),
        "pending_qty": text_number(pending_total),
        "unreceived_qty": text_number(unreceived),
        "potential_good_qty": text_number(potential_good),
        "total_cost": text_number(money(total_cost)),
        "estimated_unit_cost": text_number(unit_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        "allocated_good_cost": text_number(allocated),
        "unallocated_cost": text_number(money(total_cost) - allocated),
        "is_complete": complete,
        "allocations": allocations,
    }
