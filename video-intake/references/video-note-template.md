---
type: {{type}}
status: {{status}}
area:
- '{{area_link}}'
tags:
{{tags_block}}
related:
{{related_block}}
skill: {{skill_name}}
source_platform: "{{source_platform}}"
source_url: "{{source_url}}"
source_id: "{{source_id}}"
provider_id: "{{provider_id}}"
model_name: "{{model_name}}"
task_id: "{{task_id}}"
attachments_dir: "{{attachments_dir}}"
generated_at: "{{generated_at}}"
---
# {{title}}

## 来源信息

- 平台：`{{source_platform}}`
- 视频 ID：`{{source_id}}`
- 模型：`{{provider_id}} / {{model_name}}`
- 任务 ID：`{{task_id}}`
- 原始链接：{{source_url_markdown}}

## BiliNote 提取结果

{{body_markdown}}

## 附件

{{attachments_links}}
