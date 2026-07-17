"""Generate PRU Simulator architecture and development flow diagrams."""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np


def draw_architecture_diagram():
    """Draw the full system architecture diagram."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 12))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 12)
    ax.axis('off')
    ax.set_title('PRU Simulator — System Architecture', fontsize=16, fontweight='bold', pad=20)

    # Colors
    core_color = '#4A90D9'
    mem_color = '#7BC67E'
    io_color = '#F5A623'
    iface_color = '#9B59B6'
    xfr_color = '#E74C3C'
    bg_color = '#F8F9FA'

    # --- Simulator Orchestrator (outer box) ---
    outer = FancyBboxPatch((0.5, 0.5), 15, 11, boxstyle="round,pad=0.1",
                           facecolor=bg_color, edgecolor='#333', linewidth=2)
    ax.add_patch(outer)
    ax.text(8, 11.2, 'Simulator Orchestrator', ha='center', fontsize=12, fontweight='bold')

    # --- PRU0 Core ---
    pru0 = FancyBboxPatch((1, 6.5), 4.5, 4, boxstyle="round,pad=0.05",
                          facecolor=core_color, edgecolor='#333', linewidth=1.5, alpha=0.3)
    ax.add_patch(pru0)
    ax.text(3.25, 10.1, 'PRU0 Core (V3/V4)', ha='center', fontsize=10, fontweight='bold')

    components_pru0 = ['RegisterFile (R0-R31)', 'Decoder', 'ALU', 'BranchUnit', 'PC + Carry']
    for i, comp in enumerate(components_pru0):
        y = 9.5 - i * 0.6
        box = FancyBboxPatch((1.3, y - 0.2), 4, 0.45, boxstyle="round,pad=0.02",
                             facecolor=core_color, edgecolor='#333', linewidth=1, alpha=0.7)
        ax.add_patch(box)
        ax.text(3.3, y, comp, ha='center', va='center', fontsize=8, color='white', fontweight='bold')

    # --- RTU0 Core ---
    rtu0 = FancyBboxPatch((10.5, 6.5), 4.5, 4, boxstyle="round,pad=0.05",
                          facecolor=core_color, edgecolor='#333', linewidth=1.5, alpha=0.3)
    ax.add_patch(rtu0)
    ax.text(12.75, 10.1, 'RTU0 Core (V3/V4)', ha='center', fontsize=10, fontweight='bold')

    components_rtu0 = ['RegisterFile (R0-R31)', 'Decoder', 'ALU', 'BranchUnit', 'PC + Carry']
    for i, comp in enumerate(components_rtu0):
        y = 9.5 - i * 0.6
        box = FancyBboxPatch((10.8, y - 0.2), 4, 0.45, boxstyle="round,pad=0.02",
                             facecolor=core_color, edgecolor='#333', linewidth=1, alpha=0.7)
        ax.add_patch(box)
        ax.text(12.8, y, comp, ha='center', va='center', fontsize=8, color='white', fontweight='bold')

    # --- XFR Bus / Scratchpad ---
    xfr = FancyBboxPatch((6.2, 7.5), 3.6, 1.5, boxstyle="round,pad=0.05",
                         facecolor=xfr_color, edgecolor='#333', linewidth=1.5, alpha=0.3)
    ax.add_patch(xfr)
    ax.text(8, 8.8, 'XFR Bus', ha='center', fontsize=10, fontweight='bold')
    ax.text(8, 8.2, 'Scratchpad R2-R9', ha='center', fontsize=9)
    ax.text(8, 7.7, 'XIN / XOUT / XCHG', ha='center', fontsize=8, style='italic')

    # Arrows PRU0 <-> XFR
    ax.annotate('', xy=(6.2, 8.25), xytext=(5.5, 8.25),
                arrowprops=dict(arrowstyle='<->', color=xfr_color, lw=2))
    # Arrows RTU0 <-> XFR
    ax.annotate('', xy=(10.5, 8.25), xytext=(9.8, 8.25),
                arrowprops=dict(arrowstyle='<->', color=xfr_color, lw=2))

    # --- Memory Subsystem ---
    mem_box = FancyBboxPatch((1, 3.5), 9, 2.5, boxstyle="round,pad=0.05",
                            facecolor=mem_color, edgecolor='#333', linewidth=1.5, alpha=0.3)
    ax.add_patch(mem_box)
    ax.text(5.5, 5.7, 'Memory Subsystem (memory.cfg)', ha='center', fontsize=10, fontweight='bold')

    mem_regions = [('DRAM0\n8KB', 1.5), ('DRAM1\n8KB', 3.5), ('ICSS Shared\n32KB', 5.5),
                   ('MS_RAM\n(latency)', 7.5)]
    for label, x in mem_regions:
        box = FancyBboxPatch((x, 3.8), 1.7, 1.5, boxstyle="round,pad=0.02",
                             facecolor=mem_color, edgecolor='#333', linewidth=1, alpha=0.7)
        ax.add_patch(box)
        ax.text(x + 0.85, 4.55, label, ha='center', va='center', fontsize=8, fontweight='bold')

    # Constant Table
    ct_box = FancyBboxPatch((9.5, 3.8), 1.7, 1.5, boxstyle="round,pad=0.02",
                            facecolor=mem_color, edgecolor='#333', linewidth=1, alpha=0.5)
    ax.add_patch(ct_box)
    ax.text(10.35, 4.55, 'Constant\nTable C0-C31', ha='center', va='center', fontsize=8)

    # Arrows cores -> memory
    ax.annotate('', xy=(3.25, 6.5), xytext=(3.25, 6.0),
                arrowprops=dict(arrowstyle='<->', color='#333', lw=1.5))
    ax.annotate('', xy=(12.75, 6.5), xytext=(7, 6.0),
                arrowprops=dict(arrowstyle='<->', color='#333', lw=1.5))

    # --- IO Ports ---
    io_box = FancyBboxPatch((11, 3.5), 4.2, 2.5, boxstyle="round,pad=0.05",
                           facecolor=io_color, edgecolor='#333', linewidth=1.5, alpha=0.3)
    ax.add_patch(io_box)
    ax.text(13.1, 5.7, 'IO Subsystem', ha='center', fontsize=10, fontweight='bold')
    ax.text(13.1, 5.1, 'R30 → GPO0-19', ha='center', fontsize=9)
    ax.text(13.1, 4.6, 'R31 ← GPI0-19', ha='center', fontsize=9)
    ax.text(13.1, 4.0, 'Direct Mode', ha='center', fontsize=8, style='italic')

    # --- Interface Layer (bottom) ---
    iface_box = FancyBboxPatch((1, 0.8), 14, 2.2, boxstyle="round,pad=0.05",
                              facecolor=iface_color, edgecolor='#333', linewidth=1.5, alpha=0.2)
    ax.add_patch(iface_box)
    ax.text(8, 2.7, 'Interface Layer', ha='center', fontsize=10, fontweight='bold')

    interfaces = [('Python API\nsim.step()\nsim.registers', 2.5),
                  ('MCP Server\npru_load / pru_step\npru_registers / pru_io', 5.5),
                  ('HTML Dashboard\nWebSocket\nLive Visualization', 8.5),
                  ('VS Code (Future)\nDAP Debugger\nLSP Server', 11.5)]
    for label, x in interfaces:
        box = FancyBboxPatch((x - 0.9, 0.9), 2.5, 1.6, boxstyle="round,pad=0.02",
                             facecolor=iface_color, edgecolor='#333', linewidth=1, alpha=0.5)
        ax.add_patch(box)
        ax.text(x + 0.35, 1.7, label, ha='center', va='center', fontsize=7.5, color='white', fontweight='bold')

    plt.tight_layout()
    plt.savefig('C:/Users/a0746725/ai_code/pru_simulator/docs/architecture.png', dpi=150, bbox_inches='tight')
    plt.close()


def draw_development_flows():
    """Draw all development flows: human, AI, VS Code, and test validation."""
    fig, ax = plt.subplots(1, 1, figsize=(18, 14))
    ax.set_xlim(0, 18)
    ax.set_ylim(0, 14)
    ax.axis('off')
    ax.set_title('PRU Simulator — Development Flows', fontsize=16, fontweight='bold', pad=20)

    # Colors
    human_color = '#3498DB'
    ai_color = '#9B59B6'
    vscode_color = '#27AE60'
    test_color = '#E74C3C'
    sim_color = '#F39C12'
    arrow_kw = dict(arrowstyle='->', lw=2, connectionstyle='arc3,rad=0.1')

    # === Flow 1: Human Developer (left) ===
    ax.text(2.5, 13.5, 'Flow 1: Human Developer', ha='center', fontsize=11, fontweight='bold', color=human_color)

    human_steps = [
        (2.5, 12.5, 'Write .asm/.inc\nsource code'),
        (2.5, 11.0, 'Load in HTML\nDashboard'),
        (2.5, 9.5, 'Single-step\nexecution'),
        (2.5, 8.0, 'Observe R30/R31\nIO pins'),
        (2.5, 6.5, 'Inspect registers\n& memory'),
        (2.5, 5.0, 'Debug & iterate'),
    ]
    for x, y, text in human_steps:
        box = FancyBboxPatch((x - 1.2, y - 0.5), 2.4, 1.0, boxstyle="round,pad=0.03",
                             facecolor=human_color, edgecolor='#333', linewidth=1, alpha=0.7)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=8, color='white', fontweight='bold')

    for i in range(len(human_steps) - 1):
        ax.annotate('', xy=(2.5, human_steps[i+1][1] + 0.5), xytext=(2.5, human_steps[i][1] - 0.5),
                    arrowprops=dict(arrowstyle='->', color=human_color, lw=2))
    # Loop back arrow
    ax.annotate('', xy=(1.0, 12.5), xytext=(1.0, 5.0),
                arrowprops=dict(arrowstyle='->', color=human_color, lw=1.5, linestyle='dashed',
                               connectionstyle='arc3,rad=-0.3'))

    # === Flow 2: AI Code Generation (center-left) ===
    ax.text(6.5, 13.5, 'Flow 2: AI Agent (MCP)', ha='center', fontsize=11, fontweight='bold', color=ai_color)

    ai_steps = [
        (6.5, 12.5, 'Claude generates\nPRU assembly'),
        (6.5, 11.0, 'pru_load(asm)\nvia MCP tool'),
        (6.5, 9.5, 'pru_step(N)\nexecute'),
        (6.5, 8.0, 'pru_registers()\npru_io()'),
        (6.5, 6.5, 'Verify output\nvs expected'),
        (6.5, 5.0, 'Pass → done\nFail → regenerate'),
    ]
    for x, y, text in ai_steps:
        box = FancyBboxPatch((x - 1.2, y - 0.5), 2.4, 1.0, boxstyle="round,pad=0.03",
                             facecolor=ai_color, edgecolor='#333', linewidth=1, alpha=0.7)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=8, color='white', fontweight='bold')

    for i in range(len(ai_steps) - 1):
        ax.annotate('', xy=(6.5, ai_steps[i+1][1] + 0.5), xytext=(6.5, ai_steps[i][1] - 0.5),
                    arrowprops=dict(arrowstyle='->', color=ai_color, lw=2))
    ax.annotate('', xy=(5.0, 12.5), xytext=(5.0, 5.0),
                arrowprops=dict(arrowstyle='->', color=ai_color, lw=1.5, linestyle='dashed',
                               connectionstyle='arc3,rad=-0.3'))

    # === Flow 3: VS Code Extension (center-right) ===
    ax.text(10.5, 13.5, 'Flow 3: VS Code (Future)', ha='center', fontsize=11, fontweight='bold', color=vscode_color)

    vscode_steps = [
        (10.5, 12.5, 'Edit .asm in\nVS Code editor'),
        (10.5, 11.0, 'LSP: syntax check\nautocomplete'),
        (10.5, 9.5, 'Set breakpoints\n(DAP)'),
        (10.5, 8.0, 'F5: Start debug\nsession'),
        (10.5, 6.5, 'Step / Continue\nwatch variables'),
        (10.5, 5.0, 'IO panel shows\npin states'),
    ]
    for x, y, text in vscode_steps:
        box = FancyBboxPatch((x - 1.2, y - 0.5), 2.4, 1.0, boxstyle="round,pad=0.03",
                             facecolor=vscode_color, edgecolor='#333', linewidth=1, alpha=0.7)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=8, color='white', fontweight='bold')

    for i in range(len(vscode_steps) - 1):
        ax.annotate('', xy=(10.5, vscode_steps[i+1][1] + 0.5), xytext=(10.5, vscode_steps[i][1] - 0.5),
                    arrowprops=dict(arrowstyle='->', color=vscode_color, lw=2))

    # === Flow 4: Validation / Self-test (right) ===
    ax.text(14.5, 13.5, 'Flow 4: Validation', ha='center', fontsize=11, fontweight='bold', color=test_color)

    test_steps = [
        (14.5, 12.5, 'PRU self-test\n.asm suite'),
        (14.5, 11.0, 'Run on simulator\n(automated)'),
        (14.5, 9.5, 'Run on real\nPRU silicon'),
        (14.5, 8.0, 'Compare results\nreg-by-reg'),
        (14.5, 6.5, 'Mismatch?\nFile bug + fix sim'),
        (14.5, 5.0, 'All pass →\nconfidence ✓'),
    ]
    for x, y, text in test_steps:
        box = FancyBboxPatch((x - 1.2, y - 0.5), 2.4, 1.0, boxstyle="round,pad=0.03",
                             facecolor=test_color, edgecolor='#333', linewidth=1, alpha=0.7)
        ax.add_patch(box)
        ax.text(x, y, text, ha='center', va='center', fontsize=8, color='white', fontweight='bold')

    for i in range(len(test_steps) - 1):
        ax.annotate('', xy=(14.5, test_steps[i+1][1] + 0.5), xytext=(14.5, test_steps[i][1] - 0.5),
                    arrowprops=dict(arrowstyle='->', color=test_color, lw=2))

    # === Central Simulator Box ===
    sim_box = FancyBboxPatch((5.5, 2.5), 7, 1.8, boxstyle="round,pad=0.05",
                            facecolor=sim_color, edgecolor='#333', linewidth=2, alpha=0.8)
    ax.add_patch(sim_box)
    ax.text(9, 3.7, 'PRU Simulator Core', ha='center', fontsize=12, fontweight='bold', color='white')
    ax.text(9, 3.1, 'Python API  |  PRU0 + RTU0  |  Memory  |  XFR  |  IO',
            ha='center', fontsize=9, color='white')

    # Arrows from flows to simulator
    for x, color in [(2.5, human_color), (6.5, ai_color), (10.5, vscode_color), (14.5, test_color)]:
        ax.annotate('', xy=(max(5.5, min(12.5, x)), 4.3), xytext=(x, 4.7),
                    arrowprops=dict(arrowstyle='->', color=color, lw=2))

    # Legend
    legend_items = [
        (human_color, 'Human Developer (HTML Dashboard)'),
        (ai_color, 'AI Agent (MCP Server)'),
        (vscode_color, 'VS Code Extension (DAP/LSP) [Future]'),
        (test_color, 'Hardware Validation (Self-test Suite)'),
    ]
    for i, (color, label) in enumerate(legend_items):
        ax.add_patch(FancyBboxPatch((5.5, 1.8 - i * 0.4), 0.3, 0.25, boxstyle="round,pad=0.01",
                                    facecolor=color, edgecolor='none', alpha=0.8))
        ax.text(6.0, 1.92 - i * 0.4, label, va='center', fontsize=8)

    plt.tight_layout()
    plt.savefig('C:/Users/a0746725/ai_code/pru_simulator/docs/development_flows.png', dpi=150, bbox_inches='tight')
    plt.close()


if __name__ == '__main__':
    draw_architecture_diagram()
    draw_development_flows()
    print("Generated: docs/architecture.png")
    print("Generated: docs/development_flows.png")
