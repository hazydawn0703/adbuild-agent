<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## 工作台 UI 标准

修改工作台弹窗前阅读 [UI_DESIGN.md](UI_DESIGN.md)。表单弹窗必须复用 `app/Modal.tsx` 和 `app/modal.css`，固定标题及底部操作、仅正文滚动，使用统一浅绿色滚动条和既有表单控件；禁止恢复可编辑 JSON 界面。

所有页面和滚动容器须复用 `app/scrollbar.css` 的统一淡绿色滚动条，不单独定义配色。平台后台顶部标题栏必须吸顶，桌面及手机滚动时保持可见，遵守 `UI_DESIGN.md`。
