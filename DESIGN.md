---
name: PID Control Lab
description: 工程演示与复核人员使用的 PID 建议工作台
colors:
  console-ink: "#142a35"
  console-panel: "#213e48"
  workspace: "#f3f5f2"
  paper: "#ffffff"
  body-ink: "#1b303b"
  body-muted: "#526874"
  divider: "#dce5e2"
  signal-teal: "#087e7c"
  action-mint: "#79ddca"
typography:
  headline:
    fontFamily: '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif'
    fontSize: "30px"
    fontWeight: 760
    lineHeight: 1.3
    letterSpacing: "-0.025em"
  body:
    fontFamily: '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif'
    fontSize: "13px"
    lineHeight: 1.65
  label:
    fontFamily: '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif'
    fontSize: "12px"
    fontWeight: 650
rounded:
  control: "8px"
  surface: "12px"
spacing:
  compact: "12px"
  section: "22px"
  surface: "28px"
components:
  button-primary:
    backgroundColor: "{colors.action-mint}"
    textColor: "{colors.console-ink}"
    rounded: "{rounded.control}"
    padding: "9px 19px"
  panel:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.body-ink}"
    rounded: "{rounded.surface}"
    padding: "25px 28px"
---

# Design System: PID Control Lab

## Overview

**Creative North Star: "Signal Console"**

The interface treats each check as an inspectable signal. A dark console surface holds the primary action and run index; a light work surface keeps measurements, reasons, curves, and event history readable for sustained review. The visual system stays restrained so statuses and measured values carry the emphasis.

**Key Characteristics:**

- Dark navy anchors for action and history; light surfaces for detailed reading.
- One mint action color, with teal for selected and data states.
- Flat surfaces and fine dividers rather than decorative shadows.

## Colors

The palette uses deep blue green for control areas, white for records, and mint only for the primary action. Warning and error colors are reserved for service and request states.

## Typography

One UI sans stack carries headings, controls, and body copy. Numeric PID and metric values use tabular figures. Workspace titles are compact (15–16px); the conversation welcome uses a 26–34px heading. Message text is 14px with generous line spacing; phone inputs use 16px to avoid automatic browser zoom.

## Layout

The application fills the visual viewport. The document does not scroll. One attached sidebar holds workspace navigation and the current conversation or Monitor history. The main area holds a compact header, a workspace reading surface, and the example/simulation disclosure. Conversation text and its composer share an 800px reading width; content for technical workspaces is capped at 1400px.

The sidebar is resizable from 220px to 420px and can be collapsed. Its list scrolls separately from the content. In chat, only the transcript scrolls; the composer stays visible and grows to a bounded height. Users reading older messages retain their position when a reply arrives and can return to the latest message explicitly. Monitor retains a fixed check toolbar above its scrollable evidence and scheduler.

At 900px and below, navigation becomes a closed-by-default drawer with a vertical list, backdrop, Escape dismissal, focus containment and restoration. Selecting a destination or record dismisses it. There are no horizontal history strips. Tuning history uses a select control; catalog lists also use a select below 600px. Tuning, catalogs, and skill registration have one workspace reading surface. Dynamic viewport sizing and safe-area padding keep controls reachable on phones. Below 600px, data panels stack and the check action remains alongside its scenario selector.

## Elevation & Depth

The system is flat at rest. Tonal surfaces and 1px dividers separate tasks; focus rings provide keyboard emphasis.

## Shapes

Controls use 8px corners, content surfaces 12px corners, and small status indicators use pill shapes.

## Components

### Primary action

Mint identifies workflow actions. Message sending uses a compact deep teal button inside the composer. Phone controls remain reachable without document scrolling.

### Run history

Dark sidebar with compact records. The selected run receives a teal surface and visible outline. The same vertical list appears in the mobile navigation drawer.

### Detail sections

White surfaces separate the run summary, response curve, Agent output, and event trail. Dense values align to stable columns; the layout stacks before numbers become unreadable.

## Do's and Don'ts

- Do keep example data and undelivered recommendation labels visible.
- Do preserve complete controls and details on mobile.
- Don't use the accent for decoration or imply that a recommendation changed the actual PID.
