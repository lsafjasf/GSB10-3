"""真实时钟冒烟示例：生成若干 ID 并解码验证。"""
from snowflake import SnowflakeGenerator

gen = SnowflakeGenerator(node_id=1)
ids = [gen.next_id() for _ in range(5)]
assert ids == sorted(ids) and len(set(ids)) == 5
for i in ids:
    print(i, SnowflakeGenerator.decode(i))
