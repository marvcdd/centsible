"""Console interface.

The interface layer turns arguments into service calls and service results into
text or JSON.  It holds no business rules: anything that decides *whether*
something is allowed lives in the application or domain layer.

控制台接口层。

接口层把命令行参数转成服务调用，再把服务结果转成文本或 JSON。本层不含任何
业务规则：凡是判定某事*是否*被允许的逻辑，都放在应用层或领域层。
"""

__all__ = ["cli"]
