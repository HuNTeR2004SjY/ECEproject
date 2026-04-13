# Enterprise Context Engine (ECE)

An AI-driven enterprise solution designed to streamline ticket triage, automate problem-solving, and provide explainable insights for support workflows.

---

## 🚀 Overview

The **Enterprise Context Engine (ECE)** is a sophisticated backend system that leverages Machine Learning and specialized agents to manage enterprise support tickets. It intelligently classifies, prioritizes, and routes tickets while integrating with standard collaboration tools like Slack and Jira. 

Developed as a B.Tech project in Computer Science and Engineering, ECE focuses on **Explainable AI (XAI)** to ensure that every automated decision is transparent and auditable.

## ✨ Key Features

- **🧠 Intelligent Ticket Triage**: Automatically predicts ticket type, priority, and the optimal department queue.
- **🔍 Explainable AI (XAI)**: A dedicated wrapper provides human-readable reasoning for every triage decision.
- **🛠️ Automated Problem Solving**: Integrates specialized agents to suggest solutions and automate repetitive tasks.
- **🤝 Modern Integrations**:
  - **Slack**: Socket-mode integration for real-time ticket alerts and interaction.
  - **Jira**: Seamlessly syncing tickets with Jira projects for engineering tracking.
  - **Email**: Automated responses and heartbeats via Gmail API.
- **📊 Admin Dashboard**: Multi-company (SaaS-friendly) management interface with real-time statistics and user auditing.
- **🛡️ Audit Logging**: Detailed tracking of all user logins, ticket changes, and integration updates.

## 🛠️ Technical Stack

- **Backend**: Python, Flask, Flask-Login
- **AI/ML**: Scikit-learn, Torch, Transformers, Groq SDK
- **Data**: SQLite (Core storage), Pandas, NumPy
- **Automation**: APScheduler (Service heartbeats), Google API (Gmail)
- **Deployment**: Docker, Docker Compose
- **Documentation**: LaTeX (Full technical report included)

## 📁 Project Structure

```text
.
├── app.py                  # Main Flask application entry point
├── src/                    # Core logic and AI agents
│   ├── triage_specialist.py # ML-based classification
│   ├── problem_solver.py    # AI reasoning agent
│   ├── models.py            # Database models (User, Company, Ticket)
│   └── ...
├── Project Context/        # Complete LaTeX documentation and report
├── templates/              # Flask HTML templates (Admin & Employee views)
├── static/                 # CSS, JS, and UI assets
├── Dockerfile              # Containerization configuration
└── requirements.txt        # Python dependencies
```

## ⚙️ Installation & Setup

### Prerequisites
- Python 3.9+
- Pip (Python package manager)

### Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/HuNTeR2004SjY/ECEproject.git
   cd ECE
   ```

2. **Set up Virtual Environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Variables**:
   Create a `.env` file in the root directory and configure your keys (see `.env.example`):
   ```env
   SECRET_KEY=your_secret_key
   GROQ_API_KEY=your_groq_key
   SLACK_BOT_TOKEN=xoxb-...
   JIRA_API_TOKEN=...
   ```

5. **Run the Application**:
   ```bash
   python app.py
   ```
   The application will be available at `http://localhost:5000`.

## 📖 Usage

- **Admin Access**: Manage companies, bulk-upload employees via CSV, and configure Slack/Jira credentials.
- **Employee Access**: View assigned tickets, track triage reasoning, and interact with the AI solver.

---

## 👨‍🎓 Acknowledgments

- **Author**: Sanjay S (ASI22CS167)
- **Guide**: Ms. Shany Jophin
- **Institution**: Adi Shankara Institute of Engineering and Technology
- **Specialization**: Computer Science and Engineering
- **Year**: 2026
