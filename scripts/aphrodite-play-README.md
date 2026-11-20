# aphrodite-play 协议处理器

让网页影片管理里的「打开」按钮能调起本机默认播放器。

## 安装

```
powershell -ExecutionPolicy Bypass -File aphrodite-play-install.ps1
```

脚本做两件事：
1. 把 `aphrodite-play.bat` 写到 `%LOCALAPPDATA%\AphroditePlay\`
2. 注册表登记 `aphrodite-play://` 协议，指向该 bat

网页端的安装提示浮层里也可以直接下载/复制同样的脚本内容
（接口 `GET /api/films/handler`，内容内嵌在 `backend/films.py`，
本目录的文件仅供手动使用，两边改动要保持同步）。

## 工作原理

- 网页把本机路径拼成 `aphrodite-play://Z:/Films/xxx.mp4`（统一正斜杠）
- 处理器剥掉协议前缀后用 `start "" "<路径>"` 交给系统默认播放器
- 路径来自 `APHRODITE_FILM_PATH_MAP` 的映射换算（容器路径 → 本机路径）

## 卸载

- 删除 `%LOCALAPPDATA%\AphroditePlay` 目录
- 删除注册表项 `HKEY_CLASSES_ROOT\aphrodite-play`

## 已知限制

- 文件名含 `&`、`^` 等 cmd 特殊字符时可能解析异常（引号内大部分字符安全）
- 路径必须在本机可达（如 RaiDrive 映射盘），否则播放器会报找不到文件
