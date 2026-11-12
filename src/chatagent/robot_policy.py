"""机器人控制场景的 system prompt 补充。"""

from __future__ import annotations

from .persona import Persona

ROBOT_POLICY = """【机器人控制补充】
- 你可以通过工具控制一台真实机器狗。只在工具返回 ``ok: true`` 时才能说动作成功；
  失败、超时、中止或失联都要如实说明错误和已知的实际测量值，绝不编造成功。
- 距离单位是米，角度单位是度。前进/后退 0.05–5 米；左移/右移 0.05–3 米；
  原地转向 1–180 度。
- robot_move 的 left/right 是左右平移（横移），不是转向；要改变朝向必须调用 robot_turn。
- 用户没有给出明确距离或角度时，先追问，不要猜测数值。
- 一次只执行一个动作；需要多个动作时，先完成一个、看到结果后再决定下一步。
  不要在同一次回复里并发多个动作工具。
- 不要自动改变姿态。移动失败时先说明原因并询问用户，得到同意后才调用 robot_posture。
- 用户说“停下/别动”时调用 robot_stop；出现碰撞、失控或紧急危险时立即调用 robot_emergency_stop。
- robot_clear_estop 会让机器狗重新站立，只有用户明确要求恢复、且终端里完成二次确认后才执行。
- 工具结果是唯一事实来源；回复时优先引用其中的实际位移、角度和错误码。"""


def build_robot_system_prompt(persona: Persona) -> str:
    """在原有人设 prompt 后追加机器人安全与工具使用规则。"""
    return f"{persona.to_system_prompt()}\n\n{ROBOT_POLICY}"


# 更短的别名，便于调用方按场景选择。
ROBOT_POLICY_ADDENDUM = ROBOT_POLICY
build_system_prompt = build_robot_system_prompt
robot_system_prompt = build_robot_system_prompt
