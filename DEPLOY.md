# 检验/体检报告解读与复诊随访智能体部署说明

## 推荐方式：Render 免费 Web Service

适合课程展示。部署后会得到一个类似 `https://medical-report-agent.onrender.com` 的公网链接。

### 1. 上传代码到 GitHub

在项目根目录执行：

```powershell
git init
git add .
git commit -m "Deploy medical report agent"
```

然后在 GitHub 新建仓库，把本项目推送上去。

### 2. Render 创建服务

1. 打开 Render 控制台。
2. New -> Web Service。
3. 选择刚才的 GitHub 仓库。
4. Build Command 填：

```text
pip install -r requirements.txt
```

5. Start Command 填：

```text
python agent_app/app.py
```

6. Environment Variables 增加：

```text
HOST=0.0.0.0
DEEPSEEK_API_KEY=你的 DeepSeek API Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
```

Render 会自动提供 `PORT`，程序会自动读取。

如果暂时不想产生大模型调用费用，可以不配置 `DEEPSEEK_API_KEY`。系统会自动退回本地规则引擎和综合评估逻辑。

## Railway 部署

Railway 也可以直接识别 `Procfile`：

```text
web: python agent_app/app.py
```

如果需要手动配置：

```text
Start Command: python agent_app/app.py
```

程序会自动读取 Railway 提供的 `PORT`。

## Docker 部署

本项目已经提供 `Dockerfile`。在有 Docker 的环境中运行：

```powershell
docker build -t medical-report-agent .
docker run -p 8787:8787 medical-report-agent
```

然后访问：

```text
http://127.0.0.1:8787
```

如果部署到云服务器，需要把服务器安全组或防火墙的对应端口放行。

## 注意事项

- 当前项目是课程原型，使用 JSON 文件保存审核、随访和历史记录；Render/Railway 免费环境可能会在重启后丢失运行时新增数据。
- 如果要长期保存数据，需要改成数据库，例如 SQLite 持久磁盘、PostgreSQL 或 MySQL。
- OCR 会优先解析 PDF 文本层，再使用本地 OCR 引擎识别图片。拍照反光、模糊、表格线复杂时仍可能失败，页面会要求用户人工校对或补录指标。
- 本系统仅用于课程演示和报告解释流程展示，不构成诊断、处方或治疗建议；“用药方向”仅用于提醒患者和医生沟通，不表示可以自行用药。
