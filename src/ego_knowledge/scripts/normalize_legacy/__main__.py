"""支持 ``python -m ego_knowledge.scripts.normalize_legacy ...`` 入口。

``python -m <package>`` 不会执行包的 ``__init__`` 的 ``if __name__ == "__main__"``
分支，必须显式提供 ``__main__`` 子模块。
"""

from ego_knowledge.scripts.normalize_legacy import main

raise SystemExit(main())
