# 仿真节点插件目录

把 .py 放这里，启动时自动加载 ✓ 每个模块必须有:

    def register(reg):
        reg('节点名', label='中文名', description='会发给模型 ✓ 写清能算什么',
            params={'参数名': '类型 · 说明'}, handler=lambda args: {'结果': 1})

见 docs/SIM_NODES.md 第四节 ✓
