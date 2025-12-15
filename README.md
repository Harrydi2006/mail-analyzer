# 邮件智能日程管理系统

一个基于AI的智能邮件分析和日程管理系统，能够自动读取邮件、提取事件信息、管理日程并集成Notion进行归档。

## 🌟 主要功能

### 📧 邮件处理
- **自动邮件读取**: 支持IMAP协议，自动获取新邮件
- **智能内容分析**: 使用大模型AI分析邮件内容
- **关键词识别**: 自定义关键词自动分类邮件重要性
- **多格式支持**: 支持纯文本和HTML邮件格式

### 🤖 AI智能分析
- **事件提取**: 自动识别邮件中的时间、地点、事件信息
- **重要性评估**: AI评估邮件内容重要程度（1-10分）
- **多事件支持**: 单封邮件可提取多个时间节点和事件
- **持续时间处理**: 智能识别事件的开始时间、结束时间和持续时间

### 📅 日程管理
- **自动日程添加**: 提取的事件自动加入日程表
- **重要性分级**: 重要、普通、不重要三级分类
- **智能提醒**: 重要事件提前3天、1天、1小时提醒
- **颜色标识**: 不同重要性使用不同颜色标识
- **多视图展示**: 列表视图、时间轴视图、日历视图

### 📚 Notion集成
- **自动归档**: 邮件内容自动归档到Notion数据库
- **结构化存储**: 包含邮件信息、AI分析结果、提取事件
- **一键查看**: 从日程表直接跳转到原邮件Notion页面
- **无时间邮件**: 没有时间信息的邮件也会归档

### 🎯 Web界面
- **现代化UI**: 基于Bootstrap 5的响应式设计
- **邮件管理**: 查看邮件列表、详情、分析结果
- **日程查看**: 多种视图查看和管理日程事件
- **系统配置**: 邮件、AI、Notion等服务配置
- **关键词管理**: 可视化管理分类关键词

## 🚀 快速开始

### 环境要求

- Python 3.8+
- 支持的操作系统：Windows、Linux、macOS
- 邮箱支持IMAP协议
- AI服务API密钥（OpenAI等）
- Notion集成Token（可选）

### 安装方式

#### 方式一：直接安装

1. **克隆项目**
```bash
git clone <repository-url>
cd mail_分析
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **初始化数据库**
```bash
python main.py init-db
```

4. **启动应用**
```bash
python main.py run
```

#### 方式二：Docker部署

1. **使用Docker Compose（推荐）**
```bash
# 开发环境
docker-compose up -d

# 生产环境（包含Nginx和定时任务）
docker-compose --profile production up -d
```

2. **单独使用Docker**
```bash
# 构建镜像
docker build -t mail-scheduler .

# 运行容器
docker run -d -p 5000:5000 -v $(pwd)/data:/app/data mail-scheduler
```

### 配置说明

本项目有两个需要你**手动创建/填写**的配置文件（真实文件不会提交到仓库）：

- `config.yaml`：业务配置（邮箱/AI/Notion/数据库路径等）
- `prod.env`：生产环境变量（`SECRET_KEY` / HTTPS / Cookie 等）

仓库内提供示例文件（用于复制）：

- `config.yaml.example` → 复制为 `config.yaml`
- `prod.env.example` → 复制为 `prod.env`

在项目根目录执行：

```bash
cp config.yaml.example config.yaml
cp prod.env.example prod.env
```

然后：

- **务必修改** `prod.env` 里的 `SECRET_KEY`
- 按需编辑 `config.yaml`

#### `config.yaml` 到底起什么作用？（重点）

- **系统级默认/基础配置**：例如数据库文件路径、一些默认策略（如提醒默认规则）、应用默认端口等。
- **不是“每个用户的邮箱配置存放处”**：每个用户的邮箱/AI/Notion 等配置，都是在 Web 的「系统配置」页面里填写，并存入数据库（用户级配置）。

因此：

- 你可以把 `config.yaml` 只当作“系统默认值/兜底配置”；
- 即使 `config.yaml` 里没有填写邮箱，用户仍然可以在 Web 界面里各自配置自己的邮箱账号。

> 注意：当前 `docker-compose.yml` 默认挂载了 `./config.yaml:/app/config.yaml`，所以 **使用 Docker Compose 部署时建议仍然创建一个 `config.yaml` 文件**（可以非常简化），避免挂载路径错误。

#### 1. 邮件配置

在Web界面的「系统配置」→「邮件配置」中设置：

- **IMAP服务器**: 如 `imap.gmail.com`
- **端口**: 通常SSL为993，非SSL为143
- **用户名**: 邮箱地址
- **密码**: 邮箱密码或应用专用密码

**常见邮箱配置：**

| 邮箱服务商 | IMAP服务器 | 端口 | SSL |
|-----------|-----------|------|-----|
| Gmail | imap.gmail.com | 993 | ✓ |
| Outlook | outlook.office365.com | 993 | ✓ |
| QQ邮箱 | imap.qq.com | 993 | ✓ |
| 163邮箱 | imap.163.com | 993 | ✓ |
| 126邮箱 | imap.126.com | 993 | ✓ |

#### 2. AI服务配置

在「系统配置」→「AI配置」中设置：

- **API密钥**: OpenAI API Key
- **模型**: 推荐使用 `gpt-3.5-turbo` 或 `gpt-4`
- **基础URL**: 可选，用于自定义API端点

#### 3. Notion配置（可选）

1. 在Notion中创建集成应用：https://www.notion.so/my-integrations
2. 获取集成Token
3. 创建数据库或让系统自动创建
4. 在「系统配置」→「Notion配置」中填入Token和数据库ID

#### 4. 关键词管理

在「系统配置」→「关键词管理」中设置：

- **重要关键词**: 考试、作业、截止、deadline等
- **普通关键词**: 会议、讨论、meeting等
- **不重要关键词**: 讲座、报名、lecture等

### 使用方法

1. **首次使用**
   - 访问 http://localhost:5000
   - 进入「系统配置」完成邮件和AI服务配置
   - 点击「测试连接」确保配置正确

2. **检查邮件**
   - 点击「检查邮件」按钮手动获取新邮件
   - 系统会自动分析邮件内容并提取事件
   - 查看「邮件管理」页面了解处理结果

3. **查看日程**
   - 在「日程表」页面查看提取的事件
   - 支持列表、时间轴、日历三种视图
   - 点击事件查看详细信息

4. **管理配置**
   - 在「系统配置」中调整各项设置
   - 添加或修改关键词分类
   - 设置提醒时间和颜色

## 📁 项目结构

```
mail_分析/
├── main.py                 # 主入口文件
├── config.yaml.example     # 示例配置（复制为 config.yaml）
├── prod.env.example        # 示例环境变量（复制为 prod.env）
├── requirements.txt       # Python依赖
├── Dockerfile            # Docker配置
├── docker-compose.yml    # Docker Compose配置
├── src/                  # 源代码目录
│   ├── __init__.py
│   ├── app.py           # Flask应用
│   ├── core/            # 核心模块
│   │   ├── config.py    # 配置管理
│   │   └── logger.py    # 日志管理
│   ├── models/          # 数据模型
│   │   └── database.py  # 数据库模型
│   └── services/        # 服务层
│       ├── email_service.py    # 邮件服务
│       ├── ai_service.py       # AI服务
│       ├── scheduler_service.py # 日程服务
│       └── notion_service.py   # Notion服务
├── templates/           # HTML模板
│   ├── base.html
│   ├── index.html
│   ├── emails.html
│   ├── schedule.html
│   └── config.html
├── static/             # 静态资源
│   ├── css/
│   └── js/
├── data/              # 数据目录
└── logs/              # 日志目录
```

## 🔧 高级配置

### 环境变量

可以通过环境变量覆盖配置文件设置：

```bash
# 邮件配置
export EMAIL_USERNAME="your-email@example.com"
export EMAIL_PASSWORD="your-password"
export EMAIL_IMAP_SERVER="imap.gmail.com"

# AI配置
export AI_API_KEY="sk-..."
export AI_BASE_URL="https://api.openai.com"

# Notion配置
export NOTION_TOKEN="secret_..."
export NOTION_DATABASE_ID="..."
```

### 定时任务

系统支持定时检查邮件和处理提醒：

```bash
# 手动检查邮件
python main.py check-email

# 测试AI服务
python main.py test-ai

# 初始化数据库
python main.py init-db
```

### 生产环境部署

#### Docker Compose（推荐）

1) 准备配置文件：

- 复制 `config.yaml.example` → `config.yaml`
- 复制 `prod.env.example` → `prod.env`
- **务必修改** `prod.env` 里的 `SECRET_KEY`

如果你不想使用 `config.yaml`：

- **推荐做法**：创建一个最小的 `config.yaml`（保留默认数据库路径即可），其余都通过 Web 界面按用户配置。
- **或者**：修改 `docker-compose.yml`，删除 `./config.yaml:/app/config.yaml` 的挂载行（`mail-scheduler` 和 `scheduler` 两处都要删），然后再启动。

2) 启动：

```bash
docker compose up -d --build
```

3) 查看日志：

```bash
docker compose logs -n 200 mail-scheduler
docker compose logs -n 200 scheduler
```

> `docker-compose.yml` 中默认暴露了 443/6379 等端口，如需避免端口冲突/只允许本机访问，请自行调整端口映射（例如绑定到 `127.0.0.1`）。

## 🐛 故障排除

### 常见问题

1. **邮件连接失败**
   - 检查IMAP服务器地址和端口
   - 确认邮箱开启了IMAP服务
   - 使用应用专用密码而非登录密码
   - 检查防火墙设置

2. **AI分析失败**
   - 验证API密钥是否正确
   - 检查网络连接
   - 确认API配额是否充足
   - 查看日志文件获取详细错误信息

3. **Notion归档失败**
   - 确认Token权限正确
   - 检查数据库ID是否有效
   - 验证集成是否有数据库访问权限

4. **数据库错误**
   - 重新初始化数据库：`python main.py init-db`
   - 检查data目录权限
   - 查看日志文件了解具体错误

### 日志查看

```bash
# 查看应用日志
tail -f logs/app.log

# 查看错误日志
tail -f logs/app_error.log

# Docker环境查看日志
docker-compose logs -f mail-scheduler
```

## 🤝 贡献指南

欢迎提交Issue和Pull Request！

1. Fork项目
2. 创建功能分支：`git checkout -b feature/new-feature`
3. 提交更改：`git commit -am 'Add new feature'`
4. 推送分支：`git push origin feature/new-feature`
5. 提交Pull Request

## 📄 许可证

本项目采用MIT许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

- [Flask](https://flask.palletsprojects.com/) - Web框架
- [Bootstrap](https://getbootstrap.com/) - UI框架
- [OpenAI](https://openai.com/) - AI服务
- [Notion](https://www.notion.so/) - 知识管理平台

## 📞 支持

如果您在使用过程中遇到问题，可以：

1. 查看本文档的故障排除部分
2. 提交Issue描述问题
3. 查看项目Wiki获取更多信息

---

**注意**: 请妥善保管您的API密钥和邮箱密码，不要在公共场所或代码仓库中暴露敏感信息。