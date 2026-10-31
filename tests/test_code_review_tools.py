"""代码审查工具测试"""

from nlaut.judge.code_review_tools import (
    analyze_code_structure,
    analyze_performance,
    execute_test_cases,
    scan_security_issues,
)


def test_scan_security_issues_sql_injection():
    """测试 SQL 注入检测"""
    code = """
def search_user(name):
    query = f"SELECT * FROM users WHERE name='{name}'"
    return db.execute(query)
"""
    result = scan_security_issues(code)
    assert len(result["vulnerabilities"]) > 0
    assert any(v["type"] == "sql_injection" for v in result["vulnerabilities"])


def test_scan_security_issues_safe():
    """测试安全代码"""
    code = """
def search_user(name):
    query = "SELECT * FROM users WHERE name=?"
    return db.execute(query, (name,))
"""
    result = scan_security_issues(code)
    # 参数化查询是安全的
    sql_issues = [v for v in result["vulnerabilities"] if v["type"] == "sql_injection"]
    assert len(sql_issues) == 0


def test_execute_test_cases_pass():
    """测试用例执行 - 通过"""
    code = """
def add(a, b):
    return a + b
"""
    test_cases = [
        {"input": [1, 2], "expected": 3},
        {"input": [0, 0], "expected": 0},
    ]
    result = execute_test_cases(code, test_cases)
    assert result["summary"]["passed"] == 2
    assert result["summary"]["failed"] == 0


def test_execute_test_cases_fail():
    """测试用例执行 - 失败"""
    code = """
def concat(a, b):
    return a + b
"""
    test_cases = [
        {"input": ["hello", "world"], "expected": "helloworld"},
        {"input": [None, "world"], "expected": None},  # 会崩溃
    ]
    result = execute_test_cases(code, test_cases)
    assert result["summary"]["failed"] >= 1


def test_analyze_code_structure():
    """测试代码结构分析"""
    code = """
def add(a, b):
    return a + b

class Calculator:
    def multiply(self, a, b):
        return a * b
"""
    result = analyze_code_structure(code)
    assert len(result["functions"]) == 2
    assert len(result["classes"]) == 1
    assert result["functions"][0]["name"] == "add"


def test_analyze_performance_nested_loop():
    """测试性能分析 - 嵌套循环"""
    code = """
def find_duplicates(arr):
    result = []
    for i in range(len(arr)):
        for j in range(i+1, len(arr)):
            if arr[i] == arr[j]:
                result.append(arr[i])
    return result
"""
    result = analyze_performance(code)
    assert any(i["type"] == "nested_loop" for i in result["issues"])
    assert result["time_complexity"] == "O(n²)"


def test_analyze_performance_linear():
    """测试性能分析 - 线性复杂度"""
    code = """
def find_duplicates(arr):
    seen = set()
    result = []
    for x in arr:
        if x in seen:
            result.append(x)
        seen.add(x)
    return result
"""
    result = analyze_performance(code)
    # 单层循环，O(n)
    nested = [i for i in result["issues"] if i["type"] == "nested_loop"]
    assert len(nested) == 0


if __name__ == "__main__":
    # 运行所有测试
    test_scan_security_issues_sql_injection()
    print("✓ SQL 注入检测测试通过")

    test_scan_security_issues_safe()
    print("✓ 安全代码测试通过")

    test_execute_test_cases_pass()
    print("✓ 测试用例执行（通过）测试通过")

    test_execute_test_cases_fail()
    print("✓ 测试用例执行（失败）测试通过")

    test_analyze_code_structure()
    print("✓ 代码结构分析测试通过")

    test_analyze_performance_nested_loop()
    print("✓ 性能分析（嵌套循环）测试通过")

    test_analyze_performance_linear()
    print("✓ 性能分析（线性）测试通过")

    print("\n所有测试通过！")