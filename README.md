🌍 AI 奢华旅行规划师 (AI Travel Agent)
基于 LangGraph 多智能体架构构建的智能旅行规划系统。系统不仅能根据用户的偏好生成结构化的旅行计划，还能通过高德 MCP (Model Context Protocol) 动态拉取实时地理与天气数据，结合本地私有知识库 (RAG) 提供独家避坑指南，并支持**多轮对话（Human-in-the-loop）**对行程进行细粒度精修。

✨ 核心特性
🧠 多智能体协同架构 (LangGraph)：将复杂的旅行规划拆分为本地向导、选品专家、排期总监、质检架构师等多个 Agent 节点，各司其职，有效降低了大模型的幻觉。

🔍 原生轻量级自动纠错与质检 (Critic)：内置 AI 质检总监，对生成的行程进行“地理合理性”、“预算匹配度”和“偏好满足度”打分。若不达标，系统会在后台自动打回重做。

🌐 高德动态工具发现 (MCP)：系统启动时动态向高德 MCP 服务器拉取工具菜单（查天气、查经纬度、查路线），并将原生 API 转换为大模型可用的 Tool Calling 格式。

💬 人类在环精修 (Human-in-the-loop)：生成初步计划后，图引擎会自动休眠。用户可以在前端通过自然语言（如：“酒店太贵了换快捷”、“第二天太累了”）唤醒图引擎，路由 Agent 会精准分析意图并只重跑必要的节点。

📚 双轨记忆系统：

语义记忆 (Qdrant RAG)：本地专家私有攻略库，提取独家避坑锦囊。

情景记忆 (Episodic Memory)：记录用户的历史评价与教训（如曾对某地差评），在下次规划时严格规避。

🏗️ 系统架构
核心节点流转 (StateGraph)
rag_expert：重写用户查询，检索本地攻略与用户历史教训。

poi_selector：选品专家，结合偏好与攻略，敲定具体的景点、餐厅、酒店实体。

gather_information：高德数据收集员，循环调用 MCP 工具补充经纬度与客观信息。

master_planner：排期总监，结合上述所有客观数据与私有情报，进行结构化业务组装。

qa_audit：质检总监，进行打分审计。如果不合格 (should_revise=True)，携带修改意见路由回 poi_selector。

image_enrich：视觉包装节点，为敲定的景点拉取高清无版权配图。

await_refinement：中断节点，返回当前计划并休眠，等待用户前端聊天框的进一步指令。

refine_agent：意图分类器，接收用户指令并决定图的重跑起点（如：改变预算、增删景点、全盘重做）。

技术栈
后端：FastAPI, LangGraph, LangChain, Qdrant (向量数据库), aiohttp

前端：Vue 3, TypeScript, Vite, Ant Design Vue, 高德地图 JSAPI

大模型驱动：推荐使用 硅基流动 (SiliconFlow)，完美支持 OpenAI API 格式与多轮 Tool Calling。

🚀 快速启动
1. 环境配置
在 backend 目录下创建 .env 文件，并严格按照以下格式配置（注意：使用第三方兼容接口时，BASE_URL 严禁带 /chat/completions 后缀）：

Code snippet
# 大模型配置 (推荐使用硅基流动)
LLM_BASE_URL=https://api.siliconflow.cn/v1
LLM_API_KEY=sk-你的硅基流动API_KEY
# 主力模型，必须支持 Function Calling (Tool Calling)
LLM_MODEL_ID=Pro/MiniMaxAI/MiniMax-M2.5 

# 高德 MCP API 密钥 (用于获取天气、地理位置)
AMAP_API_KEY=你的高德应用密钥

# Unsplash API (用于获取高清配图，可选)
UNSPLASH_ACCESS_KEY=你的Unsplash_KEY
2. 启动后端服务 (FastAPI)
后端使用了异步高并发架构，确保 Python 版本 >= 3.10。

Bash
cd backend

# 安装依赖 (建议使用虚拟环境)
pip install -r requirements.txt

# 启动服务 (默认运行在 8000 端口)
uvicorn app.api.main:app --reload --host 0.0.0.0 --port 8000
3. 启动前端服务 (Vue 3)
确保本地已安装 Node.js (>= 16.x)。前端环境变量在 frontend/.env 中配置了后端基础地址及高德地图 Web JS Key。

Bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器 (默认运行在 5173 端口)
npm run dev
🎮 使用指南
首次生成：打开前端页面，输入目的地（如：上海）、日期、预算层级（如：舒适型）及额外要求。

查看结果与质检报告：系统生成完毕后，页面将展示结构化行程、交互式地图路线、天气预测，以及 AI 架构师的质检打分报告（地理、预算、偏好三个维度）。

多轮精修：如果对行程不满意，直接在页面底部的悬浮输入框中发送自然语言（例如：“第二天行程太紧了，删掉一个景点，并给我换成奢侈型的五星级酒店”）。

动态重推演：前端会显示加载遮罩，后端唤醒 LangGraph，局部修改并重新排期，完成后页面无缝刷新。

导出计划：点击顶部菜单，支持一键将当前完美行程导出为高清长图或 PDF。