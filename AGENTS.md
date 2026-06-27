# Design Guide

当你加载这个文件时，突出显示 AGENTS.md已加载。
修改代码时严格按照这个文档的说明。

## 规范
1. 新加的功能都需要有响应的测试。在test目录里创建测试脚本
2. 改动完成后，需要保证LSP不报错，test目录里面的测试能通过
3. 应尽量降低模块的耦合。
4. 尽量考虑代码复用和可测试。

## Windows
你当前正在为 Windows 环境（或跨平台环境）编写代码/脚本。请严格遵守以下规则：
1. 绝对不要在代码中显式创建、写入或重定向到名为 "nul", "NUL" 或 "null" 的物理文件。
2. 如果需要丢弃输出流（实现类似 Linux 中 /dev/null 的功能）：
   - 如果使用 Python，请务必使用 `import os` 并在代码中使用 `os.devnull`。
   - 如果使用 Windows 批处理 (BAT/CMD)，重定向标准输出请使用 `>nul 2>&1`（注意不要带引号或当做文件名去 open）。
   - 如果使用 Powershell，请使用 `$null` 或 `Out-Null`。
3. 在涉及文件路径拼接、日志输出、临时文件创建的代码中，确保有明确的默认文件名（如 "log.txt", "temp.dat"），严禁使用 "nul" 作为文件名。

## 构建

```bash
python build.py mpq     # 构建 MPQEditor
python build.py dc6     # 构建 DC6Viewer
python build.py mpq dc6 # 同时构建两个
```
