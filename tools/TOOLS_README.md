# Splight-H 工具说明

本目录只放被主搜索程序复用的通用辅助工具，不放一次性 sweep 脚本。

## exact_verify.py

用途：对 Diff 导出的完整单密钥 concrete characteristic 作 Z3 真实性验证。

输入是每轮的真实状态差分：dX、dY、dLIN、dAK、dZ，以及 rounds 和 round_offset。
验证器使用同一个主密钥建立两条真实 Splight 执行，内部按绝对轮号生成轮密钥和轮常数。

返回值严格为 SAT、UNSAT 或 UNKNOWN：

- SAT：返回一组 state/key witness，并用普通 Python 重放，只有 concrete_replay 为 PASS 才可被主程序接受。
- UNSAT：主程序可以安全地给该完整 concrete trail 加 Diff no-good。
- UNKNOWN：主程序不得排除该 concrete trail，而是将当前截断路径标为 TIMEOUT_SKIPPED。

通常不直接运行此文件。主入口 boom.py 在 --exact-verify 开启时调用 verify_trail。

## __init__.py

将 tools 标记为 Python package，使 boom.py 可以通过 tools.exact_verify 导入真实性验证器。
