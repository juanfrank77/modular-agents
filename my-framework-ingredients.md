# My Custom AI Framework - Ingredient List

> **Purpose**: This document defines the selected features, skills, and architectural patterns
> for building a custom AI agent framework. Each item includes a description of what it does,
> which reference frameworks implement it, and enough context for Claude Code to understand
> the implementation requirements.
>
> **Reference repos**: Located at `/Users/marwankashef/Desktop/YouTube/OpenClaw Antidote/`

---

**Total selected items: 23**

## Identity & Personality

### 1. Agent Profiles

**What it does**: Pre-built personas (developer, researcher, writer) you can switch between. Like changing hats.

**Reference implementations**: Agent Zero, TinyClaw


---

### 2. Dynamic Behavior Rules

**What it does**: Change how the AI behaves mid-conversation. No restart needed. It adapts immediately.

**Reference implementations**: Agent Zero, OpenClaw


---

## Security & Safety

### 1. Pairing Code Access

**What it does**: New users must enter a 6-digit code you provide before they can talk to your AI. Prevents unauthorized access.

**Reference implementations**: OpenClaw, ZeroClaw, TinyClaw


---

### 2. Command Blocklist

**What it does**: Automatically blocks commands that could damage your system (delete everything, format disks, reboot, etc.)

**Reference implementations**: PicoClaw, NanoBot, ZeroClaw


---

### 3. Execution Approval Gates

**What it does**: AI must ask your permission before running certain commands. Three levels: read-only, supervised, full autonomy.

**Reference implementations**: OpenClaw, ZeroClaw


---

## Memory & Knowledge

### 1. Solution Memory

**What it does**: AI automatically saves successful solutions. Next time it faces a similar problem, it recalls what worked before.

**Reference implementations**: Agent Zero


---

### 2. Hybrid Vector+Keyword Search

**What it does**: Combines "understanding what you mean" (semantic) with "finding exact words" (keyword). Best of both worlds for recall.

**Reference implementations**: IronClaw, ZeroClaw, Agent Zero


---

### 3. Document Knowledge Base

**What it does**: Upload PDFs, spreadsheets, documents. AI can search and analyze them. Your company's knowledge at its fingertips.

**Reference implementations**: Agent Zero, OpenClaw


---

## Automation & Scheduling

### 1. Background Sub-Agents

**What it does**: Spawn helper AI workers for long tasks. Main agent stays responsive while workers handle heavy lifting in background.

**Reference implementations**: PicoClaw, NanoBot, Agent Zero, TinyClaw


---

### 2. Agent Team Collaboration

**What it does**: Multiple specialized agents (@coder, @reviewer, @writer) pass work to each other automatically. Like a small AI company.

**Reference implementations**: TinyClaw, NanoClaw, Agent Zero


---

### 3. Browser Automation

**What it does**: AI controls a web browser: fills forms, clicks buttons, scrapes data, takes screenshots. Automates web-based work.

**Reference implementations**: OpenClaw, NanoClaw, Agent Zero, TinyClaw


---

## Integrations & Protocols

### 1. Skills System

**What it does**: Install new capabilities like apps on a phone. "Install weather skill" or "install GitHub skill." No coding needed.

**Reference implementations**: OpenClaw, PicoClaw, NanoClaw, Agent Zero, TinyClaw


---

### 2. Local LLM Support

**What it does**: Run AI models on your own computer. Zero API costs. Complete privacy. No internet needed.

**Reference implementations**: OpenClaw, ZeroClaw, PicoClaw, Agent Zero, NanoBot


---

### 3. MCP Protocol

**What it does**: Universal standard for connecting AI to external tools. One protocol, thousands of integrations. The "USB" of AI tools.

**Reference implementations**: OpenClaw, NanoBot, IronClaw, Agent Zero


---

## Built-in Skills & Ready-Made Tools

### 1. Proactive User Messaging

**What it does**: AI sends messages to you without waiting for your prompt. Alerts, reminders, status updates pushed to your chat.

**Reference implementations**: TinyClaw, Agent Zero


---

### 2. Obsidian Vault Integration

**What it does**: Work with Obsidian vaults and automate plain markdown note management.

**Reference implementations**: OpenClaw


---

### 3. GitHub Integration Skill

**What it does**: Full GitHub workflow: manage issues, review pull requests, trigger CI runs, query the API. Code management from chat.

**Reference implementations**: OpenClaw, NanoBot, PicoClaw, IronClaw, ZeroClaw


---

### 4. Document Query Tool

**What it does**: Upload documents and ask questions about them. AI extracts answers from your files.

**Reference implementations**: Agent Zero


---

### 5. Memory Store/Recall/Forget

**What it does**: Dedicated tools for saving, searching, and deleting memories. AI manages its own knowledge base.

**Reference implementations**: ZeroClaw, IronClaw, Agent Zero


---

### 6. Screenshot & Vision Tools

**What it does**: Capture screenshots, read image metadata, and analyze visual content. AI can see your screen.

**Reference implementations**: ZeroClaw, Agent Zero


---

### 7. Behaviour Adjustment Tool

**What it does**: Modify and update AI behavior rules mid-conversation. The AI rewrites its own instructions on the fly.

**Reference implementations**: Agent Zero


---

### 8. Pre-built Agent Profiles

**What it does**: Switch your AI's persona instantly. Developer mode for coding, Researcher mode for analysis, and more.

**Reference implementations**: Agent Zero


---

## Architecture Patterns

### 1. Project Workspace Isolation

**What it does**: Separate workspaces per client/project. Each has its own memory, secrets, and instructions. No data mixing.

**Reference implementations**: Agent Zero, NanoClaw


---
