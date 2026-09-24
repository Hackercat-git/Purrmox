"""Export the inventory as Markdown (for example, for a note-taking vault)."""
import time


def to_markdown(data):
    """Render nodes and guests as Markdown tables."""
    lines = ["# Homelab inventory", "", f"Generated: {time.strftime('%Y-%m-%d %H:%M')}", "",
             "## Nodes", "", "| Node | IP | Tailscale | Cores | RAM (GB) |", "|---|---|---|---|---|"]
    for n in data["nodes"]:
        lines.append(f"| {n['name']} | {n.get('ip') or '-'} | {n.get('tailscale') or '-'} | {n['cores']} | "
                     f"{n['mem_total'] / 1073741824:.1f} |")
    lines += ["", "## Virtual machines and containers", "",
              "| ID | Name | Type | Node | IP | Ports | Tags | Status |", "|---|---|---|---|---|---|---|---|"]
    for g in data["guests"]:
        ports = ", ".join(str(p["port"]) + (f" ({p['label']})" if p.get("label") else "") for p in g.get("ports", []))
        lines.append(f"| {g['id']} | {g['name']} | {g['type']} | {g['node']} | {g.get('ip') or '-'} | {ports or '-'} | "
                     f"{', '.join(g.get('tags', [])) or '-'} | {'running' if g['running'] else 'stopped'} |")
    return "\n".join(lines) + "\n"
