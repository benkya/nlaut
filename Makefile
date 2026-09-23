PY := uv run python
PYTEST := uv run pytest
RUFF := uv run ruff

.PHONY: help test lint verify regen run clean

help:
	@echo "nlaut make targets:"
	@echo "  verify      lint + test (改代码后必跑)"
	@echo "  test        框架元测试 (pytest)"
	@echo "  lint        ruff 静态检查"
	@echo "  regen       IR -> gen/ 派生代码再生成 (M2)"
	@echo "  run         执行用例, TARGET=web|electron (M2)"

test:
	$(PYTEST)

lint:
	$(RUFF) check .

verify: lint test

# M2 里程碑实现；当前 IR 库为空跑通即可
regen:
	$(PY) -m nlaut.executor.regen 2>/dev/null || echo "regen: 未实现（M2 里程碑）"

run:
	$(PY) -m nlaut.executor.runner 2>/dev/null || echo "run: 未实现（M2 里程碑）"

clean:
	rm -rf .pytest_cache .ruff_cache artifacts gen
