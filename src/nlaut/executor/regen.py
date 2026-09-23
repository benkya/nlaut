"""IR -> gen/ 派生代码再生成入口（M2）：make regen 调用。

漂移审计流：make regen && git diff cases/ —— IR 变更即资产变更，
必须走 PR 评审；gen/ 永远可重建。
"""


def regen() -> None:
    raise NotImplementedError("M2: regen 待实现")
