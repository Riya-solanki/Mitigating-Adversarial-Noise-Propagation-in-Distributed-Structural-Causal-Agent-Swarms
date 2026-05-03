# Mitigating Adversarial Noise Propagation in Distributed Structural Causal Agent Swarms

## Project Description

This project presents a next-generation **fault-tolerant multi-agent traffic management system** designed for smart cities operating under adversarial conditions.

Unlike traditional traffic control systems that rely purely on optimization or reinforcement learning, this system introduces a **causality-aware decision framework** that ensures every action taken by an agent is logically valid and globally consistent.

By combining:

- **Structural Causal Modeling (SCM)** for reasoning safety  
- **Raft Consensus** for distributed reliability  
- **Hybrid AI (MLP + LLM)** for fast and explainable decisions  

the system actively detects, isolates, and mitigates **adversarial or malfunctioning agents**, preventing cascading failures across the network.

A real-time **Digital Twin dashboard** provides full transparency into traffic flow, agent decisions, and causal dependencies, making the system both **interpretable and production-ready**.

---

## ⚙️ Key Features

### Causal Watchdog (Safety Gate)
- Validates agent actions using a **causal adjacency matrix**
- Blocks causally invalid or adversarial decisions

### Adversarial Noise Mitigation
- Detects and isolates compromised or irrational agents
- Prevents cascading system failures

### Consensus Coordination (Raft)
- Ensures consistent global state across all agents
- Handles leader election, replication, and fault tolerance

### Hybrid AI Reasoning
- **MLP (PyTorch)** → real-time action prediction  
- **Llama-3 (LLM via Ollama)** → high-level reasoning & safety checks  

### Real-Time Digital Twin
- Visualizes:
  - Traffic flow  
  - Signal states  
  - Causal dependencies  
  - System metrics  

---

## 🧱 Project Structure

```bash
├── src/
│   ├── watchdog/
│   │   └── causal_gate.py
│   ├── consensus/
│   │   └── raft_server.py
│   ├── nodes/
│   │   ├── sca_traffic_node.py
│   │   └── state_manager.py
│   ├── bridge/
│   │   └── cityflow_bridge_obj2.py
│   ├── models/
│   │   ├── llama_inference.py
│   │   └── mlp_generator.py
│
├── frontend-react/
│   └── src/components/
│       ├── CityFlowMap.jsx
│       └── Dashboard.jsx
│
├── tests/
│   ├── test_raft.py
│   ├── test_causal_gate.py
│   └── test_integration.py
│
└── docker-compose.yml
```

---

## Tech Stack

| Category            | Technology / Tool         | Purpose |
|--------------------|--------------------------|--------|
| **Languages**       | Python 3.10+             | Backend & agent logic |
|                    | JavaScript (ES6+), JSX   | Frontend |
| **Frontend**        | React 19 + Vite          | UI & dashboard |
|                    | Tailwind CSS             | Styling |
| **Backend**         | Node.js (Express)        | API layer |
| **AI / ML**         | PyTorch                  | MLP predictions |
|                    | Llama-3 (Ollama)         | Reasoning engine |
| **Distributed Sys** | Raft Protocol            | Consensus |
|                    | gRPC                     | Communication |
| **Simulation**      | CityFlow                 | Traffic simulation |
| **DevOps**          | Docker & Docker Compose  | Deployment |
| **Testing**         | Pytest                   | Validation |

---

## Installation & Setup

### Prerequisites

- Docker & Docker Compose  
- Python 3.10+  
- Ollama (for Llama-3)

---

### 🔧 Setup Steps

#### 1. Clone Repository
```bash
git clone https://github.com/your-username/your-repo-name.git
cd your-repo-name
```

#### 2. Configure Environment
```bash
cp .env.example .env
```

Update required API keys and ports.

---

#### 3. Run the System
```bash
docker-compose up --build
```

---

#### 4. Open Dashboard
```
http://localhost:5173
```

---

## Testing

Run all tests:

```bash
pytest
```

### Test Coverage

- `test_raft.py` → leader election & replication  
- `test_causal_gate.py` → adversarial validation  
- `test_integration.py` → full system workflow  

---

## System Workflow

1. Traffic agents observe environment  
2. MLP predicts optimal actions  
3. LLM evaluates complex scenarios  
4. Causal Gate validates decisions  
5. Raft ensures consensus  
6. CityFlow simulates environment  
7. Frontend visualizes results  

---

## Use Cases

- Smart city traffic optimization  
- Autonomous infrastructure systems  
- Fault-tolerant distributed AI  
- Research in causal AI and agent systems  

---

## Why This Project Stands Out

- Combines **Causal AI + Distributed Systems** (rare integration)  
- Handles **adversarial failures**, not just optimization  
- Uses **hybrid AI (MLP + LLM)** for speed + reasoning  
- Includes a **real-time digital twin visualization**  
- Built with **production-ready architecture (Docker, gRPC, Raft)**  

---

