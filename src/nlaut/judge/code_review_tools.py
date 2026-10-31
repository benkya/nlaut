"""代码审查工具集

提供 4 个专用工具用于代码审查场景：
1. execute_test_cases - 测试执行
2. scan_security_issues - 安全扫描
3. analyze_code_structure - AST 分析
4. analyze_performance - 性能分析
"""

from __future__ import annotations

import ast
import re
import subprocess
import tempfile
from typing import Any


def execute_test_cases(code: str, test_cases: list[dict], timeout: float = 5.0) -> dict:
    """执行测试用例，验证代码行为

    Args:
        code: 要测试的 Python 代码
        test_cases: 测试用例列表，每项包含 input 和 expected
        timeout: 单个测试用例的超时时间（秒）

    Returns:
        测试结果，包含每个用例的执行结果和汇总
    """
    results = []
    passed = 0
    failed = 0

    for i, tc in enumerate(test_cases):
        input_args = tc.get("input", [])
        expected = tc.get("expected")

        try:
            # 创建临时文件执行代码
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                f.write(f"\n\n# 测试调用\nresult = {code.split('def ')[1].split('(')[0]}(*{input_args})\nprint(result)\n")
                temp_file = f.name

            # 执行代码
            proc = subprocess.run(
                ["python3", temp_file],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if proc.returncode == 0:
                actual = proc.stdout.strip()
                # 尝试解析输出
                try:
                    import ast
                    actual_parsed = ast.literal_eval(actual)
                except:
                    actual_parsed = actual

                # 比较结果
                is_passed = actual_parsed == expected
                passed += 1 if is_passed else 0
                failed += 0 if is_passed else 1

                results.append({
                    "input": input_args,
                    "expected": expected,
                    "actual": actual_parsed,
                    "passed": is_passed,
                    "error": None,
                })
            else:
                # 执行出错
                failed += 1
                results.append({
                    "input": input_args,
                    "expected": expected,
                    "actual": None,
                    "passed": False,
                    "error": proc.stderr.strip(),
                })

        except subprocess.TimeoutExpired:
            failed += 1
            results.append({
                "input": input_args,
                "expected": expected,
                "actual": None,
                "passed": False,
                "error": f"Timeout after {timeout}s",
            })
        except Exception as e:
            failed += 1
            results.append({
                "input": input_args,
                "expected": expected,
                "actual": None,
                "passed": False,
                "error": str(e),
            })
        finally:
            # 清理临时文件
            try:
                import os
                os.unlink(temp_file)
            except:
                pass

    return {
        "results": results,
        "summary": {
            "total": len(test_cases),
            "passed": passed,
            "failed": failed,
            "pass_rate": passed / len(test_cases) if test_cases else 0,
        }
    }


def scan_security_issues(code: str) -> dict:
    """扫描代码安全漏洞

    检测项：
    - SQL 注入
    - 命令注入
    - 路径遍历
    - 敏感信息泄露
    - 不安全的反序列化

    Args:
        code: 要扫描的 Python 代码

    Returns:
        安全漏洞列表
    """
    vulnerabilities = []

    # 规则 1: SQL 注入
    sql_injection_patterns = [
        r'f["\'].*SELECT.*\{',  # f-string SQL
        r'\+.*SELECT.*\+',      # 字符串拼接 SQL
        r'\.format\(.*SELECT',  # format SQL
        r'execute\(.*\+',       # execute with concatenation
    ]

    for i, line in enumerate(code.split('\n'), 1):
        for pattern in sql_injection_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                vulnerabilities.append({
                    "type": "sql_injection",
                    "severity": "high",
                    "line": i,
                    "code": line.strip(),
                    "description": "用户输入可能直接拼接到 SQL 语句，存在注入风险",
                    "cwe": "CWE-89",
                    "recommendation": "使用参数化查询，如 cursor.execute('SELECT * FROM users WHERE name=?', (name,))",
                })

    # 规则 2: 命令注入
    command_injection_patterns = [
        r'os\.system\(.*\+',
        r'os\.popen\(.*\+',
        r'subprocess\..*\+',
        r'eval\(.*input',
        r'exec\(.*input',
    ]

    for i, line in enumerate(code.split('\n'), 1):
        for pattern in command_injection_patterns:
            if re.search(pattern, line):
                vulnerabilities.append({
                    "type": "command_injection",
                    "severity": "critical",
                    "line": i,
                    "code": line.strip(),
                    "description": "用户输入可能直接传入命令执行函数，存在命令注入风险",
                    "cwe": "CWE-78",
                    "recommendation": "使用 subprocess.run 并设置 shell=False，对输入进行严格校验",
                })

    # 规则 3: 路径遍历
    path_traversal_patterns = [
        r'open\(.*\+.*request',
        r'open\(.*\+.*input',
        r'\.\./',
    ]

    for i, line in enumerate(code.split('\n'), 1):
        for pattern in path_traversal_patterns:
            if re.search(pattern, line):
                vulnerabilities.append({
                    "type": "path_traversal",
                    "severity": "high",
                    "line": i,
                    "code": line.strip(),
                    "description": "用户输入可能用于构建文件路径，存在路径遍历风险",
                    "cwe": "CWE-22",
                    "recommendation": "使用 os.path.abspath 和 os.path.basename 对路径进行规范化和校验",
                })

    # 规则 4: 敏感信息泄露
    sensitive_patterns = [
        (r'password\s*=\s*["\']', "硬编码密码"),
        (r'api_key\s*=\s*["\']', "硬编码 API Key"),
        (r'secret\s*=\s*["\']', "硬编码密钥"),
    ]

    for i, line in enumerate(code.split('\n'), 1):
        for pattern, desc in sensitive_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                vulnerabilities.append({
                    "type": "hardcoded_secret",
                    "severity": "medium",
                    "line": i,
                    "code": line.strip(),
                    "description": desc,
                    "cwe": "CWE-798",
                    "recommendation": "使用环境变量或配置文件存储敏感信息",
                })

    # 规则 5: 不安全的反序列化
    unsafe_deser_patterns = [
        r'pickle\.loads\(.*request',
        r'yaml\.load\(.*request',
        r'marshal\.loads\(.*request',
    ]

    for i, line in enumerate(code.split('\n'), 1):
        for pattern in unsafe_deser_patterns:
            if re.search(pattern, line):
                vulnerabilities.append({
                    "type": "unsafe_deserialization",
                    "severity": "critical",
                    "line": i,
                    "code": line.strip(),
                    "description": "用户输入可能直接反序列化，存在代码执行风险",
                    "cwe": "CWE-502",
                    "recommendation": "使用安全的序列化格式（如 JSON）或对输入进行严格校验",
                })

    return {
        "vulnerabilities": vulnerabilities,
        "summary": {
            "total": len(vulnerabilities),
            "critical": sum(1 for v in vulnerabilities if v["severity"] == "critical"),
            "high": sum(1 for v in vulnerabilities if v["severity"] == "high"),
            "medium": sum(1 for v in vulnerabilities if v["severity"] == "medium"),
        }
    }


def analyze_code_structure(code: str, check_type: str = "all") -> dict:
    """分析代码结构

    Args:
        code: 要分析的 Python 代码
        check_type: 检查类型 (all / security / performance / boundary / concurrency / resource)

    Returns:
        代码结构分析结果
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "error": f"语法错误: {e}",
            "issues": [],
        }

    issues = []
    functions = []
    classes = []

    # 遍历 AST
    for node in ast.walk(tree):
        # 提取函数
        if isinstance(node, ast.FunctionDef):
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "args": [arg.arg for arg in node.args.args],
                "docstring": ast.get_docstring(node),
            })

            # 检查是否有返回值
            has_return = any(isinstance(n, ast.Return) for n in ast.walk(node))
            if not has_return and not any(isinstance(n, ast.Expr) for n in ast.walk(node)):
                issues.append({
                    "type": "no_return",
                    "severity": "low",
                    "line": node.lineno,
                    "description": f"函数 {node.name} 没有返回语句",
                })

        # 提取类
        if isinstance(node, ast.ClassDef):
            classes.append({
                "name": node.name,
                "line": node.lineno,
                "methods": [n.name for n in node.body if isinstance(n, ast.FunctionDef)],
            })

        # 检查未处理的异常
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append({
                "type": "bare_except",
                "severity": "medium",
                "line": node.lineno,
                "description": "使用裸 except 可能捕获意外异常",
            })

    return {
        "functions": functions,
        "classes": classes,
        "issues": issues,
        "summary": {
            "function_count": len(functions),
            "class_count": len(classes),
            "issue_count": len(issues),
        }
    }


def analyze_performance(code: str) -> dict:
    """分析代码性能

    Args:
        code: 要分析的 Python 代码

    Returns:
        性能分析结果
    """
    issues = []

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "error": f"语法错误: {e}",
            "issues": [],
        }

    # 检测嵌套循环
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            # 检查嵌套循环
            for child in ast.walk(node):
                if isinstance(child, ast.For) and child != node:
                    issues.append({
                        "type": "nested_loop",
                        "severity": "medium",
                        "line": node.lineno,
                        "description": "嵌套循环可能导致 O(n²) 复杂度，大输入时性能不可接受",
                        "recommendation": "考虑使用哈希表或集合优化到 O(n)",
                    })
                    break

        # 检测列表推导式中的重复计算
        if isinstance(node, ast.ListComp):
            # 简单检测
            pass

    # 检测重复计算
    # 使用正则表达式检测可能的重复计算
    lines = code.split('\n')
    for i, line in enumerate(lines, 1):
        # 检测在循环中重复计算的 len()
        if 'for' in line and 'len(' in line:
            match = re.search(r'for.*in range\(len\((\w+)\)\)', line)
            if match:
                issues.append({
                    "type": "repeated_len_call",
                    "severity": "low",
                    "line": i,
                    "description": f"在循环条件中重复调用 len({match.group(1)})",
                    "recommendation": "将 len() 结果缓存到变量中",
                })

    return {
        "issues": issues,
        "time_complexity": "O(n²)" if any(i["type"] == "nested_loop" for i in issues) else "O(n)",
        "space_complexity": "O(n)",  # 简化估计
        "summary": {
            "issue_count": len(issues),
            "high_severity": sum(1 for i in issues if i["severity"] == "high"),
            "medium_severity": sum(1 for i in issues if i["severity"] == "medium"),
        }
    }


# 工具注册表（供 Agent-as-Judge 使用）
CODE_REVIEW_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_test_cases",
            "description": "执行测试用例，验证代码行为是否符合预期",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要测试的 Python 代码"},
                    "test_cases": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "input": {"type": "array", "description": "输入参数列表"},
                                "expected": {"description": "期望输出"},
                            },
                        },
                        "description": "测试用例列表",
                    },
                },
                "required": ["code", "test_cases"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_security_issues",
            "description": "扫描代码安全漏洞（SQL 注入、命令注入、路径遍历等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要扫描的 Python 代码"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_code_structure",
            "description": "分析代码结构（函数、类、潜在问题）",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要分析的 Python 代码"},
                    "check_type": {
                        "type": "string",
                        "enum": ["all", "security", "performance", "boundary"],
                        "description": "检查类型",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_performance",
            "description": "分析代码性能（时间复杂度、空间复杂度、性能问题）",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要分析的 Python 代码"},
                },
                "required": ["code"],
            },
        },
    },
]


def build_code_review_tool_impls() -> dict:
    """构建代码审查工具实现

    Returns:
        工具名称 -> 实现函数的映射
    """
    return {
        "execute_test_cases": lambda args: execute_test_cases(
            code=args["code"],
            test_cases=args["test_cases"],
        ),
        "scan_security_issues": lambda args: scan_security_issues(
            code=args["code"],
        ),
        "analyze_code_structure": lambda args: analyze_code_structure(
            code=args["code"],
            check_type=args.get("check_type", "all"),
        ),
        "analyze_performance": lambda args: analyze_performance(
            code=args["code"],
        ),
    }