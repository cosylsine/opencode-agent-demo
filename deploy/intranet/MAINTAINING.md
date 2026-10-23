# 内网发布资产维护

产品源码、Tailnet 部署与本目录的内网发布模板由同一个 Git 仓库维护。原
`opencode-agent-demo-deploy/` 保留为本地发布归档和装配工作区，不再另行维护产品源码。
真实配置、数据库、账号凭据、镜像归档、离线依赖、模型文件和光盘目录不进入 Git。

## 历史基线

`INTRANET_20261021` 标签标记经核实的 2026-10-21 内网产品源码与发布模板。
`BASELINE-SOURCES.json` 记录本目录初次导入的文件来源及摘要：大部分来自实际累计
更新盘的 `payload/`，配置模板和备用 vLLM 等材料来自原完整包。历史发布所依赖的
镜像摘要保存在 `images/IMAGE-MANIFEST.json`；数据库版本为 `0007`。

这些历史模板原样保存，包括发布号、旧镜像标签和部分制作机路径。本文件及源码导出器
是在建立历史标签之后增加的维护能力。不要移动历史标签，也不要用开发分支的源码覆盖
原 20261021 光盘或声称它仍对应原镜像。

## 导出指定源码和模板

在具备导出器的当前主线工作区运行；目标的父目录必须存在，目标目录必须尚不存在：

```bash
python3 scripts/export_intranet_source.py \
  --ref INTRANET_20261021 \
  --output ../intranet-source-20261021-review
```

也可以把 `--ref` 换成完整提交 ID。导出器先把引用解析为提交，再始终从该提交读取，
不会混入当前修改、暂存内容或未跟踪文件。产物布局为：

```text
intranet-source-20261021-review/
├── SOURCE-EXPORT.json       提交、tree、请求引用、逐文件路径及 SHA-256
├── source/                  产品源码及其他原有 deploy/ 模板
├── Dockerfile
├── deploy.sh
├── build/
├── config/                  仅示例配置
├── scripts/
└── images/、runtime/、vllm/  已入库的来源及摘要元数据
```

`deploy/intranet/` 从产品源码中移出，内容摊平到导出根目录；其他 `deploy/` 文件仍位于
`source/deploy/`。Git 工作目录元数据不导出；源码中的 `.gitignore` 和 `.opencode`
等受版本管理的配置仍然保留。导出器拒绝已存在的目标、路径穿越、符号链接、特殊文件、
子模块及模板对 `source/` 等保留路径的占用，避免产生越界或不完整的源码副本。

**这只是源码和模板装配，不是完整离线包。** `SOURCE-EXPORT.json` 明确设置
`complete_offline_bundle=false`。导出时不会复制二进制、生成镜像、制作光盘、改动旧包，
也不能直接把该目录当作可运行交付物。历史摘要不能证明修改后的源码对应旧镜像；
未来每次正式发布都需要重新构建、测试并生成对应的镜像及运输清单。

## 本地离线资产

20261021 原始镜像和 runtime 继续保存在原本地归档中。使用前必须按对应发布的清单校验，
并按清单的相对路径选择工作目录。例如：

```bash
cd /本地归档/opencode-agent-demo-deploy
sha256sum -c images/SHA256SUMS
sha256sum -c runtime/SHA256SUMS
sha256sum -c runtime/nvidia-cdi/SHA256SUMS
(cd runtime/ripgrep && sha256sum -c SHA256SUMS)
sha256sum -c RELEASE-SHA256SUMS --quiet
```

独立的 `runtime/SHA256SUMS` 只覆盖 Docker 静态归档，不能替代整个 runtime 的校验；
20261021 的 `RELEASE-SHA256SUMS` 还覆盖办公依赖等实际交付文件。若本地归档的源码或
模板已被修改，完整发布校验会失败，应回到保留的原盘 `payload/` 进行核对，不能重新生成
摘要来掩盖差异。参考模板和来源清单也应与选定 Git 提交的元数据相符。

仅在需要备用模型服务时校验它的独立资产：

```bash
sha256sum -c vllm/SHA256SUMS
sha256sum -c vllm/COMPAT-SHA256SUMS
sha256sum -c vllm/MODEL-METADATA-SHA256SUMS
```

现有应用 `Dockerfile` 依赖已经导入的
`opencode-agent-demo-runtime:20261018-office-amd64` 和本地 `runtime/ripgrep/rg`，
办公 runtime 又建立在原基础依赖镜像上。`build/Dockerfile.runtime` 引用的 `vendor/`
输入没有全部纳入仓库，因此当前能力是**复用固定离线依赖镜像重建应用**，并不表示仅凭
Git 即可从零重建整个基础镜像。联网收集依赖、许可证和完整供应清单是后续独立工作。

## 累计更新盘与新装包

`scripts/make-intranet-disc.py` 和 `scripts/intranet-update.sh` 是 **20261021 累计更新**
流程：保留现场配置与数据，以指定旧部署为前提，并要求已有日志和验收文件。部分证据
只保存在本地归档，所以源码导出目录不能直接调用它来重新制作原盘。
该累计盘不包含新装配置模板和完整备用 vLLM 资产；完整安装包不能与累计更新盘混用。

`scripts/make-dvd.sh` 仍假定归档顶层目录名为 `opencode-agent-demo-deploy`；
在适配并重新验证它之前，不要在任意改名的导出目录运行。
`vllm.sh` 和历史演练程序还包含旧应用镜像标签；发布前需要随新版本逐项更新并验证。

可复用的验证源码保存在 `verification/`，其中固定的测试账号只用于隔离的 TEST 模型
演练，不是现场登录凭据。部分历史工具固定了制作机 Docker socket 和工作目录，需要在
后续维护提交中改为显式参数才能跨机器复跑。`verification/release-20261021/RESULTS.md`
保留当时结论；它引用的原始日志不随 Git 发布，仍在本地交付归档中。

本轮只建立源码导出和版本追溯能力，不重制 20261021 光盘，也不替代下一次发布的构建、
隔离验收、更新/回退演练和实际刻盘读回校验。
