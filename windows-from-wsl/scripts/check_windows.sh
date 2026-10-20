#!/usr/bin/env bash
# 只读：从 WSL 检查已授权 Windows 宿主机与指定挂载盘 Git 项目。
set -euo pipefail

if [[ $# != 1 ]]; then
  echo "用法：bash check_windows.sh /mnt/d/ws/目标项目" >&2
  exit 2
fi
case "$(uname -r)" in
  *[Mm]icrosoft*) ;;
  *) echo "仅支持从 WSL 执行" >&2; exit 2 ;;
esac
project_dir=$(realpath -e -- "$1")
case "$project_dir" in
  /mnt/c/*|/mnt/d/*) ;;
  *) echo "需要 /mnt/c 或 /mnt/d 下的真实项目目录" >&2; exit 2 ;;
esac
[[ -d "$project_dir" ]] || { echo "目标不是目录" >&2; exit 2; }

echo "目标仓根："
git -c safe.directory='*' -C "$project_dir" rev-parse --show-toplevel
echo "本次 Git 命令的 safe.directory（未写配置）："
git -c safe.directory='*' -C "$project_dir" config --get-all safe.directory

ssh -o BatchMode=yes -o ConnectTimeout=10 windows-host-codex 'bash -s' <<'WINDOWS'
set -euo pipefail
echo "SSH 用户："
whoami
echo "pwsh 版本（UTF-8）："
pwsh -NoLogo -NoProfile -NonInteractive -Command '[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $PSVersionTable.PSVersion.ToString()'
WINDOWS
