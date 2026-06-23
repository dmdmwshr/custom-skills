# 年度产品问题汇总路由

本目录只服务“年度产品监督底册问题汇总”条线，不承载月度成绩登记规则。

## 按用户任务加载

- 用户要求生成或审计年度产品问题汇总：读取 `source_product_register.md`、`output_annual_problem_summary.md`、`validation_and_audit.md`。
- 用户只问底册问题如何解析：读取 `source_product_register.md` 和 `validation_and_audit.md`。
- 用户只问 Word 汇总格式：读取 `output_annual_problem_summary.md`。
- 用户修改年度汇总规则：先读取 `../skill_maintenance.md`，再读取本路由和对应对象文档。

## 脚本到文档

- `annual_product_problem_summary.py`：年度底册发现、底册问题解析、年度汇总 Word 生成、review JSON 审计。

## 维护要求

年度汇总条线独立于月度 R/G 输出。修改本条线时，不得改变 `monthly_grade_register.py` 的月度成品口径，除非用户明确要求同步调整月度流程。
