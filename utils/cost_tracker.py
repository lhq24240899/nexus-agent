"""
成本监控 —— 对应视频中"搞完之后看看花了多少钱"
真实值实现:
  1. token 数来自 API 返回的 usage 字段 (真实)
  2. 单价从 .env 读取 (可配置)
  3. 支持账单校准: 输入实际花费, 反算真实单价并保存

性能策略: record 高频调用, 采用防抖落盘 (1s 内的多条记录合并写一次),
进程退出时 atexit 兜底 flush, 查询始终走内存不受影响。
"""
import atexit
import json
import threading
import time
from config import DATA_DIR, COST_CONFIG

COST_FILE = DATA_DIR / "cost_log.json"
CALIB_FILE = DATA_DIR / "cost_calibration.json"
_SAVE_DEBOUNCE_SECONDS = 1.0


class CostTracker:
    """记录每次 API 调用的 token 消耗和费用, 支持账单校准"""

    def __init__(self):
        self.records: list[dict] = []
        self.calibrated_prices: dict[str, dict] = {}  # model -> {input, output}
        self._lock = threading.RLock()
        self._dirty = False
        self._save_timer: threading.Timer | None = None
        self._load()
        self._load_calibration()
        atexit.register(self.flush)

    def _load(self):
        if COST_FILE.exists():
            self.records = json.loads(COST_FILE.read_text(encoding="utf-8"))

    def _schedule_save(self):
        with self._lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(
                _SAVE_DEBOUNCE_SECONDS, self.flush
            )
            self._save_timer.daemon = True
            self._save_timer.start()

    def flush(self):
        """立即落盘 (Timer 回调 / atexit / calibrate 后手动调用)"""
        with self._lock:
            if not self._dirty:
                return
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
            COST_FILE.write_text(
                json.dumps(self.records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._dirty = False

    def _load_calibration(self):
        if CALIB_FILE.exists():
            self.calibrated_prices = json.loads(
                CALIB_FILE.read_text(encoding="utf-8")
            )

    def _save_calibration(self):
        CALIB_FILE.write_text(
            json.dumps(self.calibrated_prices, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _price(self, model: str) -> dict:
        """优先用校准后的单价, 否则用配置单价"""
        if model in self.calibrated_prices:
            return self.calibrated_prices[model]
        return COST_CONFIG.get(model, COST_CONFIG["default"])

    def record(self, model: str, input_tokens: int, output_tokens: int,
               task: str = ""):
        price = self._price(model)
        cost = (input_tokens / 1_000_000 * price["input"]
                + output_tokens / 1_000_000 * price["output"])
        entry = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_yuan": round(cost, 4),
            "task": task[:40],
        }
        with self._lock:
            self.records.append(entry)
            self._dirty = True
        self._schedule_save()
        return entry

    def calibrate(self, model: str, actual_cost_yuan: float,
                  period: str = "today") -> dict:
        """
        账单校准: 根据实际花费反算真实单价
        period: 'today' 校准今日数据, 'all' 校准全部历史
        返回校准结果
        """
        if period == "today":
            today = time.strftime("%Y-%m-%d")
            records = [r for r in self.records
                       if r["time"].startswith(today) and r["model"] == model]
        else:
            records = [r for r in self.records if r["model"] == model]

        if not records:
            return {"ok": False, "error": f"没有找到 {model} 的调用记录"}

        total_input = sum(r["input_tokens"] for r in records)
        total_output = sum(r["output_tokens"] for r in records)
        total_tokens = total_input + total_output

        if total_tokens == 0:
            return {"ok": False, "error": "token 总数为 0"}

        # 按当前输入输出比例反算单价
        ratio = total_input / total_tokens if total_tokens else 0.5
        avg_price = actual_cost_yuan / (total_tokens / 1_000_000)
        input_price = round(avg_price * ratio * 2, 4)
        output_price = round(avg_price * (1 - ratio) * 2, 4)

        with self._lock:
            self.calibrated_prices[model] = {
                "input": input_price,
                "output": output_price,
                "calibrated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "based_on_records": len(records),
                "actual_cost": actual_cost_yuan,
            }
            self._save_calibration()

            # 用新单价重算历史记录
            for r in self.records:
                if r["model"] == model:
                    p = self.calibrated_prices[model]
                    r["cost_yuan"] = round(
                        r["input_tokens"] / 1_000_000 * p["input"]
                        + r["output_tokens"] / 1_000_000 * p["output"], 4
                    )
            self._dirty = True
            self.flush()

        return {
            "ok": True,
            "model": model,
            "calibrated_input_price": input_price,
            "calibrated_output_price": output_price,
            "based_on_records": len(records),
            "total_tokens": total_tokens,
            "actual_cost": actual_cost_yuan,
        }

    def total_today(self) -> dict:
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            today_records = [r for r in self.records
                             if r["time"].startswith(today)]
        total_cost = sum(r["cost_yuan"] for r in today_records)
        total_input = sum(r["input_tokens"] for r in today_records)
        total_output = sum(r["output_tokens"] for r in today_records)

        # 按模型分组
        by_model = {}
        for r in today_records:
            m = r["model"]
            if m not in by_model:
                by_model[m] = {"calls": 0, "input": 0, "output": 0, "cost": 0.0}
            by_model[m]["calls"] += 1
            by_model[m]["input"] += r["input_tokens"]
            by_model[m]["output"] += r["output_tokens"]
            by_model[m]["cost"] += r["cost_yuan"]

        return {
            "date": today,
            "calls": len(today_records),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_cost_yuan": round(total_cost, 4),
            "by_model": {k: {**v, "cost": round(v["cost"], 4)}
                         for k, v in by_model.items()},
            "calibrated_models": list(self.calibrated_prices.keys()),
        }

    def history(self, n: int = 20) -> list[dict]:
        with self._lock:
            return self.records[-n:]

    def get_prices(self) -> dict:
        """获取当前生效的单价 (含校准)"""
        result = {}
        for model in set(list(COST_CONFIG.keys()) +
                         list(self.calibrated_prices.keys())):
            result[model] = self._price(model)
        return result


cost_tracker = CostTracker()
