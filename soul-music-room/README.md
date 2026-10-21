# MoodFlow 前端

MoodFlow 的音乐播放与 AI 助手网页界面，使用 React、TypeScript 和 Vite，包含音乐搜索、歌单、播放器、AI 对话与 WorldLine 音乐关系探索。

完整项目的环境准备与 Docker 启动步骤见[根目录 README](../README.md)，服务端配置与测试说明见[开发指南](../DEVELOPMENT.md)。单独启动前端需要已经运行的音乐后端和 AI 服务。

## 本地开发

使用项目根目录 `.nvmrc` 指定的 Node.js 版本。在当前目录运行：

```bash
npm ci
npm run dev
```

默认开发地址为 `http://localhost:5173`。音乐请求默认访问 `http://localhost:8080`，AI 请求通过 Vite 代理到 `http://127.0.0.1:8001`。需要改变地址时，可在本目录未提交的 `.env.local` 中设置 `VITE_API_BASE_URL` 和 `VITE_AI_API_BASE_URL`；这些变量用于浏览器请求地址，不应包含服务端密钥。

## 目录

| 目录 | 内容 |
| --- | --- |
| `src/pages/` | 页面与路由界面 |
| `src/components/` | 播放器、聊天、音乐探索等组件 |
| `src/api/`、`src/services/` | 音乐和 AI 接口、播放与客户端动作 |
| `src/store/` | Zustand 状态管理 |
| `src/assets/`、`public/` | 界面使用的图片和静态资源 |
| `scripts/test-*.mjs` | 当前功能的自动化回归检查 |

## 构建与检查

```bash
npm run build
npm run lint
npm run test:ai-layout
npm run test:ai-stream
npm run test:music-assets
npm run test:search
npm run test:memory
npm run test:ai-trigger
npm run test:worldline
```

构建产物位于 `dist/`。`npm run preview` 用于本地预览构建结果；完整部署由根目录 Compose 配置提供服务端和同源代理。

## 许可与来源

本模块使用 [GPL-3.0-only](LICENSE)。第三方依赖与改编代码的说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)、[粒子背景署名](src/components/background/ATTRIBUTION.md)和[玻璃效果署名](src/components/liquid-glass/ATTRIBUTION.md)。
