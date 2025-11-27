#!/usr/bin/env python3
"""
Report Generator - Generates beautiful HTML reports from MCP test JSON files.

Usage:
    python report_generator.py <reports_folder> [--output <output_file>]
    
Example:
    python report_generator.py ./reports --output my_report.html
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def load_json_files(folder_path: str) -> list[dict]:
    """Load all JSON files from the specified folder."""
    reports = []
    folder = Path(folder_path)
    
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    
    for json_file in sorted(folder.glob("*.json")):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                data["_source_file"] = json_file.name
                reports.append(data)
        except json.JSONDecodeError as e:
            print(f"Warning: Could not parse {json_file.name}: {e}")
    
    return reports


def analyze_reports(reports: list[dict]) -> dict[str, Any]:
    """Analyze all reports and compile statistics."""
    stats = {
        "total_reports": len(reports),
        "total_tests": 0,
        "passed_tests": 0,
        "failed_tests": 0,
        "total_duration_seconds": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_tokens": 0,
        "total_cost_usd": 0,
        "models_used": {},
        "providers_used": {},
        "executors_used": {},
        "tests_by_date": {},
        "tests_by_model": {},
        "cost_by_model": {},
        "duration_by_model": {},
        "detailed_results": [],
        "errors": [],
    }
    
    for report in reports:
        # Extract summary info
        summary = report.get("summary", {})
        
        # Count tests
        total = summary.get("total", summary.get("executed", 0))
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        duration = summary.get("duration_seconds", 0)
        
        stats["total_tests"] += total
        stats["passed_tests"] += passed
        stats["failed_tests"] += failed
        stats["total_duration_seconds"] += duration
        
        # Token usage
        token_usage = report.get("token_usage_total", {})
        input_tokens = token_usage.get("input_tokens", 0)
        output_tokens = token_usage.get("output_tokens", 0)
        total_tokens = token_usage.get("total_tokens", 0)
        cost_usd = token_usage.get("cost_usd", 0)
        
        stats["total_input_tokens"] += input_tokens
        stats["total_output_tokens"] += output_tokens
        stats["total_tokens"] += total_tokens
        stats["total_cost_usd"] += cost_usd
        
        # Model and provider tracking
        model = report.get("llm_model", "unknown")
        provider = report.get("llm_provider", "unknown")
        executor = report.get("executor", "unknown")
        
        stats["models_used"][model] = stats["models_used"].get(model, 0) + 1
        stats["providers_used"][provider] = stats["providers_used"].get(provider, 0) + 1
        stats["executors_used"][executor] = stats["executors_used"].get(executor, 0) + 1
        
        # Tests by model
        if model not in stats["tests_by_model"]:
            stats["tests_by_model"][model] = {"passed": 0, "failed": 0, "total": 0}
        stats["tests_by_model"][model]["passed"] += passed
        stats["tests_by_model"][model]["failed"] += failed
        stats["tests_by_model"][model]["total"] += total
        
        # Cost by model
        stats["cost_by_model"][model] = stats["cost_by_model"].get(model, 0) + cost_usd
        
        # Duration by model
        if model not in stats["duration_by_model"]:
            stats["duration_by_model"][model] = []
        stats["duration_by_model"][model].append(duration)
        
        # Date tracking
        generated_at = report.get("generated_at", "")
        if generated_at:
            try:
                date_str = generated_at[:10]  # Get YYYY-MM-DD
                stats["tests_by_date"][date_str] = stats["tests_by_date"].get(date_str, 0) + total
            except Exception:
                pass
        
        # Detailed results
        for result in report.get("results", []):
            result_detail = {
                "source_file": report.get("_source_file", ""),
                "name": result.get("name", ""),
                "url": result.get("url", ""),
                "passed": result.get("passed", result.get("success", False)),
                "duration_seconds": result.get("duration_seconds", 0),
                "model": model,
                "provider": provider,
                "generated_at": generated_at,
                "token_usage": result.get("token_usage", {}),
                "steps_count": len(result.get("steps_executed", [])),
                "screenshots_count": len(result.get("screenshots", [])),
                "error": result.get("error"),
            }
            stats["detailed_results"].append(result_detail)
            
            # Track errors
            error = result.get("error")
            if error:
                stats["errors"].append({
                    "test": result.get("name", ""),
                    "error": error,
                    "file": report.get("_source_file", ""),
                })
    
    # Calculate averages
    if stats["total_tests"] > 0:
        stats["avg_duration_per_test"] = stats["total_duration_seconds"] / stats["total_tests"]
        stats["avg_tokens_per_test"] = stats["total_tokens"] / stats["total_tests"]
        stats["avg_cost_per_test"] = stats["total_cost_usd"] / stats["total_tests"]
    else:
        stats["avg_duration_per_test"] = 0
        stats["avg_tokens_per_test"] = 0
        stats["avg_cost_per_test"] = 0
    
    # Calculate average duration by model
    stats["avg_duration_by_model"] = {}
    for model, durations in stats["duration_by_model"].items():
        if durations:
            stats["avg_duration_by_model"][model] = sum(durations) / len(durations)
    
    # Pass rate
    if stats["total_tests"] > 0:
        stats["pass_rate"] = (stats["passed_tests"] / stats["total_tests"]) * 100
    else:
        stats["pass_rate"] = 0
    
    return stats


def format_duration(seconds: float) -> str:
    """Format duration in a human-readable way."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.2f}h"


def format_number(num: float) -> str:
    """Format large numbers with K/M suffix."""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    return str(int(num))


def generate_html_report(stats: dict[str, Any], output_path: str) -> None:
    """Generate a beautiful HTML report."""
    
    # Prepare chart data
    models_labels = json.dumps(list(stats["tests_by_model"].keys()))
    models_passed = json.dumps([v["passed"] for v in stats["tests_by_model"].values()])
    models_failed = json.dumps([v["failed"] for v in stats["tests_by_model"].values()])
    
    cost_labels = json.dumps(list(stats["cost_by_model"].keys()))
    cost_values = json.dumps([round(v, 4) for v in stats["cost_by_model"].values()])
    
    dates_labels = json.dumps(sorted(stats["tests_by_date"].keys()))
    dates_values = json.dumps([stats["tests_by_date"][d] for d in sorted(stats["tests_by_date"].keys())])
    
    avg_duration_labels = json.dumps(list(stats["avg_duration_by_model"].keys()))
    avg_duration_values = json.dumps([round(v, 2) for v in stats["avg_duration_by_model"].values()])
    
    # Generate detailed results table rows
    results_rows = ""
    for result in stats["detailed_results"]:
        status_class = "passed" if result["passed"] else "failed"
        status_icon = "✓" if result["passed"] else "✗"
        token_info = result.get("token_usage", {})
        tokens_str = format_number(token_info.get("total_tokens", 0)) if token_info else "N/A"
        cost_str = f"${token_info.get('cost_usd', 0):.4f}" if token_info else "N/A"
        
        results_rows += f"""
        <tr class="{status_class}">
            <td><span class="status-badge {status_class}">{status_icon}</span></td>
            <td class="test-name">{result['name']}</td>
            <td>{result['model']}</td>
            <td>{format_duration(result['duration_seconds'])}</td>
            <td>{tokens_str}</td>
            <td>{cost_str}</td>
            <td>{result['steps_count']}</td>
            <td>{result['screenshots_count']}</td>
        </tr>
        """
    
    # Generate errors section
    errors_html = ""
    if stats["errors"]:
        errors_html = """
        <section class="card errors-section">
            <h2>⚠️ Erros Encontrados</h2>
            <div class="errors-list">
        """
        for error in stats["errors"]:
            error_text = str(error["error"])[:500] + "..." if len(str(error["error"])) > 500 else error["error"]
            errors_html += f"""
            <div class="error-item">
                <div class="error-header">
                    <strong>{error['test']}</strong>
                    <span class="error-file">{error['file']}</span>
                </div>
                <pre class="error-message">{error_text}</pre>
            </div>
            """
        errors_html += "</div></section>"
    
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Relatório de Testes MCP</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-primary: #0d1117;
            --bg-secondary: #161b22;
            --bg-tertiary: #21262d;
            --border-color: #30363d;
            --text-primary: #f0f6fc;
            --text-secondary: #8b949e;
            --accent-green: #3fb950;
            --accent-red: #f85149;
            --accent-blue: #58a6ff;
            --accent-purple: #a371f7;
            --accent-orange: #d29922;
            --accent-cyan: #39d5ff;
            --gradient-1: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --gradient-2: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            --gradient-3: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Space Grotesk', sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            line-height: 1.6;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
        }}
        
        header {{
            text-align: center;
            margin-bottom: 3rem;
            padding: 2rem;
            background: var(--bg-secondary);
            border-radius: 16px;
            border: 1px solid var(--border-color);
            position: relative;
            overflow: hidden;
        }}
        
        header::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: var(--gradient-1);
        }}
        
        h1 {{
            font-size: 2.5rem;
            font-weight: 700;
            background: var(--gradient-1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 0.5rem;
        }}
        
        .subtitle {{
            color: var(--text-secondary);
            font-size: 1rem;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
        
        .stat-card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            position: relative;
            overflow: hidden;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
        }}
        
        .stat-card:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.3);
        }}
        
        .stat-card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
        }}
        
        .stat-card.green::before {{ background: var(--accent-green); }}
        .stat-card.red::before {{ background: var(--accent-red); }}
        .stat-card.blue::before {{ background: var(--accent-blue); }}
        .stat-card.purple::before {{ background: var(--accent-purple); }}
        .stat-card.orange::before {{ background: var(--accent-orange); }}
        .stat-card.cyan::before {{ background: var(--accent-cyan); }}
        
        .stat-card h3 {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 0.5rem;
        }}
        
        .stat-card .value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 2rem;
            font-weight: 600;
            color: var(--text-primary);
        }}
        
        .stat-card .subvalue {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-top: 0.25rem;
        }}
        
        .card {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 2rem;
        }}
        
        .card h2 {{
            font-size: 1.25rem;
            margin-bottom: 1.5rem;
            padding-bottom: 0.75rem;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        
        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
        
        .chart-container {{
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
        }}
        
        .chart-container h3 {{
            font-size: 1rem;
            color: var(--text-secondary);
            margin-bottom: 1rem;
            text-align: center;
        }}
        
        .chart-wrapper {{
            position: relative;
            height: 250px;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
        }}
        
        th, td {{
            padding: 0.875rem 1rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }}
        
        th {{
            background: var(--bg-tertiary);
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            font-size: 0.75rem;
            letter-spacing: 0.5px;
        }}
        
        tr:hover {{
            background: var(--bg-tertiary);
        }}
        
        .status-badge {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 24px;
            height: 24px;
            border-radius: 50%;
            font-weight: 600;
            font-size: 0.85rem;
        }}
        
        .status-badge.passed {{
            background: rgba(63, 185, 80, 0.2);
            color: var(--accent-green);
        }}
        
        .status-badge.failed {{
            background: rgba(248, 81, 73, 0.2);
            color: var(--accent-red);
        }}
        
        tr.passed:hover {{
            background: rgba(63, 185, 80, 0.05);
        }}
        
        tr.failed:hover {{
            background: rgba(248, 81, 73, 0.05);
        }}
        
        .test-name {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.85rem;
            max-width: 400px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        
        .pass-rate-bar {{
            width: 100%;
            height: 8px;
            background: var(--bg-tertiary);
            border-radius: 4px;
            overflow: hidden;
            margin-top: 1rem;
        }}
        
        .pass-rate-fill {{
            height: 100%;
            background: var(--gradient-1);
            border-radius: 4px;
            transition: width 0.5s ease;
        }}
        
        .errors-section {{
            border-left: 4px solid var(--accent-red);
        }}
        
        .error-item {{
            background: var(--bg-tertiary);
            border-radius: 8px;
            padding: 1rem;
            margin-bottom: 1rem;
        }}
        
        .error-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.5rem;
        }}
        
        .error-file {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}
        
        .error-message {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.8rem;
            color: var(--accent-red);
            background: rgba(248, 81, 73, 0.1);
            padding: 0.75rem;
            border-radius: 6px;
            overflow-x: auto;
            white-space: pre-wrap;
            word-break: break-word;
        }}
        
        .model-breakdown {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1rem;
        }}
        
        .model-card {{
            background: var(--bg-tertiary);
            border-radius: 8px;
            padding: 1rem;
        }}
        
        .model-card h4 {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            color: var(--accent-purple);
            margin-bottom: 0.75rem;
        }}
        
        .model-stat {{
            display: flex;
            justify-content: space-between;
            padding: 0.25rem 0;
            font-size: 0.85rem;
        }}
        
        .model-stat span:first-child {{
            color: var(--text-secondary);
        }}
        
        .model-stat span:last-child {{
            font-family: 'JetBrains Mono', monospace;
        }}
        
        /* Cost Simulator Styles */
        .simulator-section {{
            border-left: 4px solid var(--accent-purple);
        }}
        
        .simulator-container {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 2rem;
            align-items: start;
        }}
        
        .simulator-controls {{
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }}
        
        .model-select {{
            width: 100%;
            padding: 0.875rem 1rem;
            background: var(--bg-tertiary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            color: var(--text-primary);
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.9rem;
            cursor: pointer;
            transition: border-color 0.2s ease;
        }}
        
        .model-select:hover {{
            border-color: var(--accent-purple);
        }}
        
        .model-select:focus {{
            outline: none;
            border-color: var(--accent-purple);
            box-shadow: 0 0 0 3px rgba(163, 113, 247, 0.2);
        }}
        
        .model-select option {{
            background: var(--bg-secondary);
            color: var(--text-primary);
            padding: 0.5rem;
        }}
        
        .simulator-results {{
            background: var(--bg-tertiary);
            border-radius: 12px;
            padding: 1.5rem;
        }}
        
        .simulator-results h4 {{
            font-size: 0.85rem;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 1rem;
        }}
        
        .sim-cost-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.75rem 0;
            border-bottom: 1px solid var(--border-color);
        }}
        
        .sim-cost-row:last-child {{
            border-bottom: none;
            padding-top: 1rem;
            margin-top: 0.5rem;
            border-top: 2px solid var(--accent-purple);
        }}
        
        .sim-cost-label {{
            color: var(--text-secondary);
            font-size: 0.9rem;
        }}
        
        .sim-cost-value {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.1rem;
            font-weight: 600;
        }}
        
        .sim-cost-value.input {{
            color: var(--accent-cyan);
        }}
        
        .sim-cost-value.output {{
            color: var(--accent-orange);
        }}
        
        .sim-cost-value.total {{
            color: var(--accent-green);
            font-size: 1.5rem;
        }}
        
        .sim-comparison {{
            margin-top: 1rem;
            padding: 1rem;
            background: rgba(163, 113, 247, 0.1);
            border-radius: 8px;
            text-align: center;
        }}
        
        .sim-comparison .diff {{
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.25rem;
            font-weight: 600;
        }}
        
        .sim-comparison .diff.savings {{
            color: var(--accent-green);
        }}
        
        .sim-comparison .diff.increase {{
            color: var(--accent-red);
        }}
        
        .model-info {{
            margin-top: 1rem;
            padding: 1rem;
            background: var(--bg-tertiary);
            border-radius: 8px;
            font-size: 0.85rem;
        }}
        
        .model-info-row {{
            display: flex;
            justify-content: space-between;
            padding: 0.25rem 0;
            color: var(--text-secondary);
        }}
        
        .model-info-row span:last-child {{
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-primary);
        }}
        
        @media (max-width: 768px) {{
            .simulator-container {{
                grid-template-columns: 1fr;
            }}
        }}
        
        footer {{
            text-align: center;
            padding: 2rem;
            color: var(--text-secondary);
            font-size: 0.85rem;
        }}
        
        @media (max-width: 768px) {{
            .container {{
                padding: 1rem;
            }}
            
            h1 {{
                font-size: 1.75rem;
            }}
            
            .stats-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
            
            .charts-grid {{
                grid-template-columns: 1fr;
            }}
            
            .chart-container {{
                min-width: auto;
            }}
            
            table {{
                font-size: 0.8rem;
            }}
            
            th, td {{
                padding: 0.5rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 Relatório de Testes MCP</h1>
            <p class="subtitle">Gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M:%S')}</p>
        </header>
        
        <!-- Summary Stats -->
        <div class="stats-grid">
            <div class="stat-card blue">
                <h3>Total de Relatórios</h3>
                <div class="value">{stats['total_reports']}</div>
            </div>
            <div class="stat-card purple">
                <h3>Total de Testes</h3>
                <div class="value">{stats['total_tests']}</div>
            </div>
            <div class="stat-card green">
                <h3>Testes Passados</h3>
                <div class="value">{stats['passed_tests']}</div>
                <div class="subvalue">{stats['pass_rate']:.1f}% taxa de sucesso</div>
            </div>
            <div class="stat-card red">
                <h3>Testes Falhados</h3>
                <div class="value">{stats['failed_tests']}</div>
            </div>
            <div class="stat-card cyan">
                <h3>Duração Total</h3>
                <div class="value">{format_duration(stats['total_duration_seconds'])}</div>
                <div class="subvalue">~{format_duration(stats['avg_duration_per_test'])} por teste</div>
            </div>
            <div class="stat-card orange">
                <h3>Custo Total</h3>
                <div class="value">${stats['total_cost_usd']:.4f}</div>
                <div class="subvalue">~${stats['avg_cost_per_test']:.4f} por teste</div>
            </div>
        </div>
        
        <!-- Token Usage Stats -->
        <div class="stats-grid">
            <div class="stat-card purple">
                <h3>Input Tokens</h3>
                <div class="value">{format_number(stats['total_input_tokens'])}</div>
            </div>
            <div class="stat-card cyan">
                <h3>Output Tokens</h3>
                <div class="value">{format_number(stats['total_output_tokens'])}</div>
            </div>
            <div class="stat-card blue">
                <h3>Total Tokens</h3>
                <div class="value">{format_number(stats['total_tokens'])}</div>
                <div class="subvalue">~{format_number(stats['avg_tokens_per_test'])} por teste</div>
            </div>
        </div>
        
        <!-- Cost Simulator -->
        <section class="card simulator-section">
            <h2>💰 Simulador de Custos</h2>
            <p style="color: var(--text-secondary); margin-bottom: 1.5rem;">
                Simule quanto custaria executar os mesmos testes com diferentes modelos da OpenAI
            </p>
            <div class="simulator-container">
                <div class="simulator-controls">
                    <label for="model-simulator" style="color: var(--text-secondary); font-size: 0.9rem;">
                        Selecione um modelo para simular:
                    </label>
                    <select id="model-simulator" class="model-select" onchange="updateCostSimulation()">
                        <optgroup label="⭐ Referência">
                            <option value="original-implied" selected>Preço Original (baseado no custo real)</option>
                        </optgroup>
                        <optgroup label="OpenAI - GPT-5 (estimado)">
                            <option value="gpt-5.1">GPT-5.1 ($2.00 / $8.00 por 1M tokens)</option>
                            <option value="gpt-5">GPT-5 ($1.25 / $5.00 por 1M tokens)</option>
                            <option value="gpt-5-mini">GPT-5 Mini ($0.20 / $0.80 por 1M tokens)</option>
                            <option value="gpt-5-nano">GPT-5 Nano ($0.05 / $0.20 por 1M tokens)</option>
                        </optgroup>
                        <optgroup label="OpenAI - GPT-4o Family">
                            <option value="gpt-4o">GPT-4o ($2.50 / $10.00 por 1M tokens)</option>
                            <option value="gpt-4o-mini">GPT-4o Mini ($0.15 / $0.60 por 1M tokens)</option>
                        </optgroup>
                        <optgroup label="OpenAI - GPT-4 Legacy">
                            <option value="gpt-4-turbo">GPT-4 Turbo ($10.00 / $30.00 por 1M tokens)</option>
                            <option value="gpt-4">GPT-4 ($30.00 / $60.00 por 1M tokens)</option>
                        </optgroup>
                        <optgroup label="OpenAI - GPT-3.5">
                            <option value="gpt-3.5-turbo">GPT-3.5 Turbo ($0.50 / $1.50 por 1M tokens)</option>
                        </optgroup>
                        <optgroup label="OpenAI - Reasoning (o-series)">
                            <option value="o1">o1 ($15.00 / $60.00 por 1M tokens)</option>
                            <option value="o1-mini">o1-mini ($3.00 / $12.00 por 1M tokens)</option>
                            <option value="o3-mini">o3-mini ($1.10 / $4.40 por 1M tokens)</option>
                        </optgroup>
                        <optgroup label="Anthropic - Claude">
                            <option value="claude-3-5-sonnet">Claude 3.5 Sonnet ($3.00 / $15.00 por 1M tokens)</option>
                            <option value="claude-3-5-haiku">Claude 3.5 Haiku ($0.80 / $4.00 por 1M tokens)</option>
                            <option value="claude-3-opus">Claude 3 Opus ($15.00 / $75.00 por 1M tokens)</option>
                        </optgroup>
                        <optgroup label="Google - Gemini">
                            <option value="gemini-1.5-pro">Gemini 1.5 Pro ($1.25 / $5.00 por 1M tokens)</option>
                            <option value="gemini-1.5-flash">Gemini 1.5 Flash ($0.075 / $0.30 por 1M tokens)</option>
                            <option value="gemini-2.0-flash">Gemini 2.0 Flash ($0.10 / $0.40 por 1M tokens)</option>
                        </optgroup>
                        <optgroup label="DeepSeek">
                            <option value="deepseek-chat">DeepSeek Chat ($0.14 / $0.28 por 1M tokens)</option>
                            <option value="deepseek-reasoner">DeepSeek Reasoner ($0.55 / $2.19 por 1M tokens)</option>
                        </optgroup>
                    </select>
                    
                    <div class="model-info">
                        <div class="model-info-row">
                            <span>Input Tokens Totais:</span>
                            <span id="sim-input-tokens">{format_number(stats['total_input_tokens'])}</span>
                        </div>
                        <div class="model-info-row">
                            <span>Output Tokens Totais:</span>
                            <span id="sim-output-tokens">{format_number(stats['total_output_tokens'])}</span>
                        </div>
                        <div class="model-info-row">
                            <span>Custo Original:</span>
                            <span id="sim-original-cost">${stats['total_cost_usd']:.4f}</span>
                        </div>
                    </div>
                </div>
                
                <div class="simulator-results">
                    <h4>Custo Simulado</h4>
                    <div class="sim-cost-row">
                        <span class="sim-cost-label">Custo de Input:</span>
                        <span class="sim-cost-value input" id="sim-input-cost">$0.0000</span>
                    </div>
                    <div class="sim-cost-row">
                        <span class="sim-cost-label">Custo de Output:</span>
                        <span class="sim-cost-value output" id="sim-output-cost">$0.0000</span>
                    </div>
                    <div class="sim-cost-row">
                        <span class="sim-cost-label">Custo Total Simulado:</span>
                        <span class="sim-cost-value total" id="sim-total-cost">$0.0000</span>
                    </div>
                    
                    <div class="sim-comparison">
                        <div style="color: var(--text-secondary); font-size: 0.85rem; margin-bottom: 0.5rem;">
                            Comparado ao custo original:
                        </div>
                        <div class="diff" id="sim-diff">$0.0000 (0%)</div>
                    </div>
                </div>
            </div>
        </section>
        
        <!-- Pass Rate Bar -->
        <section class="card">
            <h2>📈 Taxa de Sucesso Geral</h2>
            <div style="font-size: 2rem; font-weight: 700; color: var(--accent-green);">
                {stats['pass_rate']:.1f}%
            </div>
            <div class="pass-rate-bar">
                <div class="pass-rate-fill" style="width: {stats['pass_rate']}%;"></div>
            </div>
        </section>
        
        <!-- Charts -->
        <div class="charts-grid">
            <div class="chart-container">
                <h3>Resultados por Modelo</h3>
                <div class="chart-wrapper">
                    <canvas id="modelResultsChart"></canvas>
                </div>
            </div>
            <div class="chart-container">
                <h3>Custo por Modelo (USD)</h3>
                <div class="chart-wrapper">
                    <canvas id="costChart"></canvas>
                </div>
            </div>
            <div class="chart-container">
                <h3>Testes por Data</h3>
                <div class="chart-wrapper">
                    <canvas id="dateChart"></canvas>
                </div>
            </div>
            <div class="chart-container">
                <h3>Duração Média por Modelo (s)</h3>
                <div class="chart-wrapper">
                    <canvas id="durationChart"></canvas>
                </div>
            </div>
        </div>
        
        <!-- Model Breakdown -->
        <section class="card">
            <h2>🤖 Detalhamento por Modelo</h2>
            <div class="model-breakdown">
                {"".join([f'''
                <div class="model-card">
                    <h4>{model}</h4>
                    <div class="model-stat">
                        <span>Total de Testes:</span>
                        <span>{data['total']}</span>
                    </div>
                    <div class="model-stat">
                        <span>Passados:</span>
                        <span style="color: var(--accent-green);">{data['passed']}</span>
                    </div>
                    <div class="model-stat">
                        <span>Falhados:</span>
                        <span style="color: var(--accent-red);">{data['failed']}</span>
                    </div>
                    <div class="model-stat">
                        <span>Taxa de Sucesso:</span>
                        <span>{(data['passed']/data['total']*100) if data['total'] > 0 else 0:.1f}%</span>
                    </div>
                    <div class="model-stat">
                        <span>Custo Total:</span>
                        <span>${stats['cost_by_model'].get(model, 0):.4f}</span>
                    </div>
                    <div class="model-stat">
                        <span>Duração Média:</span>
                        <span>{format_duration(stats['avg_duration_by_model'].get(model, 0))}</span>
                    </div>
                </div>
                ''' for model, data in stats['tests_by_model'].items()])}
            </div>
        </section>
        
        {errors_html}
        
        <!-- Detailed Results Table -->
        <section class="card">
            <h2>📋 Resultados Detalhados</h2>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Status</th>
                            <th>Nome do Teste</th>
                            <th>Modelo</th>
                            <th>Duração</th>
                            <th>Tokens</th>
                            <th>Custo</th>
                            <th>Passos</th>
                            <th>Screenshots</th>
                        </tr>
                    </thead>
                    <tbody>
                        {results_rows}
                    </tbody>
                </table>
            </div>
        </section>
        
        <footer>
            <p>Relatório gerado automaticamente pelo MCP Report Generator</p>
        </footer>
    </div>
    
    <script>
        // Chart.js configuration
        Chart.defaults.color = '#8b949e';
        Chart.defaults.borderColor = '#30363d';
        
        // Model Results Chart
        new Chart(document.getElementById('modelResultsChart'), {{
            type: 'bar',
            data: {{
                labels: {models_labels},
                datasets: [
                    {{
                        label: 'Passados',
                        data: {models_passed},
                        backgroundColor: 'rgba(63, 185, 80, 0.7)',
                        borderColor: '#3fb950',
                        borderWidth: 1
                    }},
                    {{
                        label: 'Falhados',
                        data: {models_failed},
                        backgroundColor: 'rgba(248, 81, 73, 0.7)',
                        borderColor: '#f85149',
                        borderWidth: 1
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'bottom'
                    }}
                }},
                scales: {{
                    x: {{
                        stacked: true
                    }},
                    y: {{
                        stacked: true,
                        beginAtZero: true
                    }}
                }}
            }}
        }});
        
        // Cost Chart
        new Chart(document.getElementById('costChart'), {{
            type: 'doughnut',
            data: {{
                labels: {cost_labels},
                datasets: [{{
                    data: {cost_values},
                    backgroundColor: [
                        'rgba(163, 113, 247, 0.8)',
                        'rgba(88, 166, 255, 0.8)',
                        'rgba(57, 213, 255, 0.8)',
                        'rgba(210, 153, 34, 0.8)',
                        'rgba(63, 185, 80, 0.8)'
                    ],
                    borderColor: '#161b22',
                    borderWidth: 2
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'bottom'
                    }}
                }}
            }}
        }});
        
        // Date Chart
        new Chart(document.getElementById('dateChart'), {{
            type: 'line',
            data: {{
                labels: {dates_labels},
                datasets: [{{
                    label: 'Testes Executados',
                    data: {dates_values},
                    borderColor: '#58a6ff',
                    backgroundColor: 'rgba(88, 166, 255, 0.1)',
                    fill: true,
                    tension: 0.4
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        display: false
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});
        
        // Duration Chart
        new Chart(document.getElementById('durationChart'), {{
            type: 'bar',
            data: {{
                labels: {avg_duration_labels},
                datasets: [{{
                    label: 'Duração Média (s)',
                    data: {avg_duration_values},
                    backgroundColor: 'rgba(57, 213, 255, 0.7)',
                    borderColor: '#39d5ff',
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        display: false
                    }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});
        
        // ========================================
        // Cost Simulator
        // ========================================
        
        // Token data from the report (must be defined first)
        const tokenData = {{
            inputTokens: {stats['total_input_tokens']},
            outputTokens: {stats['total_output_tokens']},
            originalCost: {stats['total_cost_usd']}
        }};
        
        // Model prices per million tokens (input / output)
        // Prices based on OpenAI official pricing (Nov 2024) + estimated GPT-5 prices
        const modelPrices = {{
            // GPT-5 Family (estimated prices)
            'gpt-5.1': {{ input: 2.00, output: 8.00, name: 'GPT-5.1' }},
            'gpt-5': {{ input: 1.25, output: 5.00, name: 'GPT-5' }},
            'gpt-5-mini': {{ input: 0.20, output: 0.80, name: 'GPT-5 Mini' }},
            'gpt-5-nano': {{ input: 0.05, output: 0.20, name: 'GPT-5 Nano' }},
            // GPT-4o Family (current main models)
            'gpt-4o': {{ input: 2.50, output: 10.00, name: 'GPT-4o' }},
            'gpt-4o-mini': {{ input: 0.15, output: 0.60, name: 'GPT-4o Mini' }},
            'gpt-4o-audio-preview': {{ input: 2.50, output: 10.00, name: 'GPT-4o Audio' }},
            // GPT-4 Family (legacy)
            'gpt-4-turbo': {{ input: 10.00, output: 30.00, name: 'GPT-4 Turbo' }},
            'gpt-4': {{ input: 30.00, output: 60.00, name: 'GPT-4' }},
            // GPT-3.5
            'gpt-3.5-turbo': {{ input: 0.50, output: 1.50, name: 'GPT-3.5 Turbo' }},
            // Reasoning models (o-series)
            'o1': {{ input: 15.00, output: 60.00, name: 'o1' }},
            'o1-mini': {{ input: 3.00, output: 12.00, name: 'o1-mini' }},
            'o1-preview': {{ input: 15.00, output: 60.00, name: 'o1-preview' }},
            'o3-mini': {{ input: 1.10, output: 4.40, name: 'o3-mini' }},
            // Claude (Anthropic)
            'claude-3-5-sonnet': {{ input: 3.00, output: 15.00, name: 'Claude 3.5 Sonnet' }},
            'claude-3-5-haiku': {{ input: 0.80, output: 4.00, name: 'Claude 3.5 Haiku' }},
            'claude-3-opus': {{ input: 15.00, output: 75.00, name: 'Claude 3 Opus' }},
            // Gemini (Google)
            'gemini-1.5-pro': {{ input: 1.25, output: 5.00, name: 'Gemini 1.5 Pro' }},
            'gemini-1.5-flash': {{ input: 0.075, output: 0.30, name: 'Gemini 1.5 Flash' }},
            'gemini-2.0-flash': {{ input: 0.10, output: 0.40, name: 'Gemini 2.0 Flash' }},
            // DeepSeek
            'deepseek-chat': {{ input: 0.14, output: 0.28, name: 'DeepSeek Chat' }},
            'deepseek-reasoner': {{ input: 0.55, output: 2.19, name: 'DeepSeek Reasoner' }}
        }};
        
        // Calculate and add implied price from original data (if available)
        if (tokenData.originalCost > 0 && tokenData.inputTokens > 0) {{
            // Assume 8:1 ratio for output:input pricing (common pattern)
            const totalWeightedTokens = tokenData.inputTokens + (tokenData.outputTokens * 8);
            const impliedInputPrice = (tokenData.originalCost * 1_000_000) / totalWeightedTokens;
            modelPrices['original-implied'] = {{
                input: impliedInputPrice,
                output: impliedInputPrice * 8,
                name: 'Preço Original (calculado)'
            }};
        }}
        
        function formatCurrency(value) {{
            return '$' + value.toFixed(4);
        }}
        
        function formatPercentage(value) {{
            const sign = value >= 0 ? '+' : '';
            return sign + value.toFixed(1) + '%';
        }}
        
        function updateCostSimulation() {{
            const modelSelect = document.getElementById('model-simulator');
            const selectedModel = modelSelect.value;
            const prices = modelPrices[selectedModel];
            
            if (!prices) return;
            
            let inputCost, outputCost, totalCost;
            
            // Special handling for original-implied price
            if (selectedModel === 'original-implied') {{
                // Use the exact original cost to avoid rounding issues
                totalCost = tokenData.originalCost;
                // Calculate proportional split (rough estimate)
                const inputRatio = tokenData.inputTokens / (tokenData.inputTokens + tokenData.outputTokens * 8);
                inputCost = totalCost * inputRatio;
                outputCost = totalCost - inputCost;
            }} else {{
                // Calculate costs (prices are per million tokens)
                inputCost = (tokenData.inputTokens / 1_000_000) * prices.input;
                outputCost = (tokenData.outputTokens / 1_000_000) * prices.output;
                totalCost = inputCost + outputCost;
            }}
            
            // Update display
            document.getElementById('sim-input-cost').textContent = formatCurrency(inputCost);
            document.getElementById('sim-output-cost').textContent = formatCurrency(outputCost);
            document.getElementById('sim-total-cost').textContent = formatCurrency(totalCost);
            
            // Calculate difference from original
            const diff = totalCost - tokenData.originalCost;
            const diffPercent = tokenData.originalCost > 0 
                ? ((diff / tokenData.originalCost) * 100) 
                : 0;
            
            const diffElement = document.getElementById('sim-diff');
            
            if (tokenData.originalCost === 0) {{
                diffElement.textContent = formatCurrency(totalCost) + ' (custo original não disponível)';
                diffElement.className = 'diff';
            }} else if (selectedModel === 'original-implied') {{
                diffElement.textContent = '✓ Mesmo valor do custo original';
                diffElement.className = 'diff';
            }} else if (Math.abs(diff) < 0.0001) {{
                diffElement.textContent = '≈ Mesmo custo';
                diffElement.className = 'diff';
            }} else if (diff < 0) {{
                // Savings
                diffElement.textContent = formatCurrency(Math.abs(diff)) + ' economia (' + formatPercentage(diffPercent) + ')';
                diffElement.className = 'diff savings';
            }} else {{
                // Increase
                diffElement.textContent = formatCurrency(diff) + ' a mais (' + formatPercentage(diffPercent) + ')';
                diffElement.className = 'diff increase';
            }}
        }}
        
        // Initialize simulator on page load
        document.addEventListener('DOMContentLoaded', function() {{
            updateCostSimulation();
        }});
        
        // Also run immediately in case DOM is already loaded
        if (document.readyState !== 'loading') {{
            updateCostSimulation();
        }}
    </script>
</body>
</html>
"""
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"✅ Relatório gerado com sucesso: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Gera relatórios HTML bonitos a partir de arquivos JSON de testes MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python report_generator.py ./reports
  python report_generator.py ./reports --output meu_relatorio.html
  python report_generator.py /caminho/absoluto/reports -o relatorio.html
        """
    )
    parser.add_argument(
        "folder",
        help="Caminho para a pasta contendo os arquivos JSON de relatórios"
    )
    parser.add_argument(
        "-o", "--output",
        default="report.html",
        help="Nome do arquivo HTML de saída (padrão: report.html)"
    )
    
    args = parser.parse_args()
    
    print(f"📂 Lendo relatórios de: {args.folder}")
    
    try:
        reports = load_json_files(args.folder)
        
        if not reports:
            print("❌ Nenhum arquivo JSON encontrado na pasta especificada.")
            return 1
        
        print(f"📊 {len(reports)} arquivo(s) JSON encontrado(s)")
        
        stats = analyze_reports(reports)
        
        print(f"📈 Estatísticas compiladas:")
        print(f"   - Total de testes: {stats['total_tests']}")
        print(f"   - Passados: {stats['passed_tests']}")
        print(f"   - Falhados: {stats['failed_tests']}")
        print(f"   - Taxa de sucesso: {stats['pass_rate']:.1f}%")
        print(f"   - Custo total: ${stats['total_cost_usd']:.4f}")
        
        generate_html_report(stats, args.output)
        
        return 0
        
    except FileNotFoundError as e:
        print(f"❌ Erro: {e}")
        return 1
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        raise


if __name__ == "__main__":
    exit(main())

