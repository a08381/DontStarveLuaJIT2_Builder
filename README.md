# DontStarveLuaJIT2_Builder

本仓库负责自动构建并发布 [DontStarveLuaJIT2](https://github.com/fesily/DontStarveLuaJIT2) 的 Linux x86_64 二进制包。构建目标是让产物所需的最高 glibc 符号版本不超过 **GLIBC_2.28**，以兼容较旧的 Linux 发行版。

## 仓库内容

本仓库是构建与发布配置仓库，不包含上游项目的完整源代码：

- `.github/workflows/build.yml`：检查上游更新、构建、验证并发布 stable/nightly 包。
- `.github/workflows/cleanup-build-runs.yml`：定期清理过期的构建运行记录。
- `COMMIT_ID_stable`、`COMMIT_ID_nightly`：由发布工作流维护，记录已成功构建的上游提交，用于跳过未变化的通道。
- 根目录中的 ZIP、SHA-256 校验文件和 `glibc-report.txt`：由发布工作流更新；首次成功发布前可能尚不存在。

构建时，工作流会将上游源码及其子模块检出到工作区的 `DontStarveLuaJIT2/` 目录。这里生成的 Linux 包是从上游源码构建而来；不要将该工作区目录与本仓库自身的源码混淆。

## 自动构建流程

`build.yml` 每小时检查一次上游，也可以从 GitHub Actions 手动运行。

1. `stable` 跟随上游最新 release；没有 release 时依次回退到最新 tag、指定分支。
2. `nightly` 跟随指定的上游分支，默认是 `master`。
3. 将解析到的提交与 `COMMIT_ID_stable`、`COMMIT_ID_nightly` 比较，只构建有变化的通道。
4. 在 `manylinux_2_28_x86_64` 容器中使用 CMake、Ninja 和探测到的 GCC 工具链构建上游项目。
5. 整理依赖并为 ELF 设置运行时搜索路径；隔离可能与游戏进程冲突的依赖 SONAME。
6. 保留上游预置的 Lua 5.1 签名参考库，只将 `libm.so.6` 的 `GLIBC_2.29` 数学符号版本需求映射到 glibc 2.28 已提供的 `GLIBC_2.2.5` ABI，不替换 Lua 函数机器码。
7. 检查 ELF 的 GLIBC 符号版本不高于 `GLIBC_2.28`，生成 `glibc-report.txt`。
8. 打包并生成 SHA-256 校验文件；通过 ABI smoke 检查后，由单独的发布任务把成功构建的通道产物和提交记录更新到仓库。

ABI smoke 检查在 AlmaLinux 8（glibc 2.28）和 Ubuntu 22.04 环境中解包产物，使用 `ldd -r` 检查库及符号解析，并注入测试用的 `LD_LIBRARY_PATH` 诱饵，以验证自带依赖没有意外回退到系统库。

## 产物

仓库根目录保留每个通道最新的包及其 SHA-256 文件：

| 通道 | 文件 |
| --- | --- |
| stable | `DontStarveLuaJIT2-stable-linux-x86_64-glibc228.zip` |
| nightly | `DontStarveLuaJIT2-nightly-linux-x86_64-glibc228.zip` |

每个 ZIP 都有同名的 `.sha256` 校验文件。`glibc-report.txt` 列出本次扫描的 ELF 文件及其最高 GLIBC/GLIBCXX 符号版本。工作流运行产生的 Actions artifact 默认保留 7 天。

GLIBC 版本扫描是二进制兼容性检查，不代表对所有发行版、驱动或运行环境的完整兼容保证；使用前仍应结合目标机器验证。

## 手动运行参数

在 **Actions → build-linux-glibc228 → Run workflow** 中可以调整以下选项：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `force_build` | `false` | 忽略已记录的提交 ID，强制构建 stable 和 nightly。 |
| `purge_history` | `false` | 从 Git 历史中清除旧归档。此操作会重写历史并强制推送，可能影响其他克隆仓库；仅在确有需要时启用。 |
| `upstream_repo` | `fesily/DontStarveLuaJIT2` | 上游源码仓库，格式为 `owner/repo`。 |
| `upstream_branch` | `master` | nightly 跟随的分支；stable 在没有 release/tag 时也会用作回退。 |
| `build_type` | `RelWithDebInfo` | CMake 配置：`RelWithDebInfo`、`Release` 或 `Debug`。 |
| `libstdcxx` | `static` | `static` 将 libstdc++/libgcc 静态链接；`bundle` 则把运行库放入包内 `Mod/deps`。 |
| `steam_mode` | `auto` | Steamworks SDK 获取策略：`auto`、`secret`、`stub` 或 `none`。 |
| `frida_mode` | `auto` | frida-gum 获取策略：`auto`、`devkit` 或 `meson`。Meson 全量构建耗时较长。 |

`steam_mode=secret` 需要配置名为 `STEAMWORKS_SDK_B64` 的仓库 Actions secret，内容为 SDK 压缩包的 Base64 编码。`auto` 会优先使用该 secret（如果已配置），并在缺少 SDK 时尝试公开头文件和链接桩库回退。使用 `stub` 或 `none` 可能会使相关 Steam 功能不可用。

工作流发布需要仓库 Actions token 具有 `contents: write` 权限。

## 过期运行记录清理

`cleanup-build-runs.yml` 每天 UTC 04:23 运行一次，删除超过 7 天的失败、超时或没有成功构建任务的记录；包含成功构建任务的运行记录会保留。
