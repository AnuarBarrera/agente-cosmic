# Quick Start Guide

Get DIALOGIX running in under 10 minutes! This guide will have you up and running with a basic chatbot setup.

## Prerequisites

Before you begin, ensure you have:

- [x] **Python 3.8+** installed
- [x] **Node.js 16+** installed  
- [x] **PostgreSQL** database
- [x] **Google Gemini API Key** ([Get one here](https://aistudio.google.com/))

## 🚀 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/dialogix/chatbot.git
cd chatbot
```

### 2. Backend Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run database migrations
python manage.py migrate

# Create superuser (optional)
python manage.py createsuperuser

# Start development server
python manage.py runserver
```

The backend will be running at `http://localhost:8000`

### 3. Frontend Setup

Open a new terminal window:

```bash
cd frontend
npm install
npm start
```

The frontend will be running at `http://localhost:3000`

## ⚙️ Basic Configuration

### 1. Environment Variables

Create a `.env` file in the root directory:

```bash
# Database
DATABASE_URL=postgresql://username:password@localhost:5432/dialogix

# Django
SECRET_KEY=your-secret-key-here
DEBUG=True

# JWT Settings
JWT_SECRET_KEY=your-jwt-secret

```

### 2. Database Setup

```bash
# Create PostgreSQL database
createdb dialogix

# Run migrations
python manage.py migrate
```

## 🎯 First Steps

### 1. Create Your Account

1. Navigate to `http://localhost:3000`
2. Click **Register** 
3. Fill in your tenant information
4. Verify your email (check your inbox)
5. Login to your dashboard

### 2. Configure AI Integration

1. Go to **Settings** → **AI Configuration**
2. Add your Google Gemini API Key
3. Test the connection
4. Save your configuration

!!! tip "Getting a Gemini API Key"
    
    1. Visit [Google AI Studio](https://aistudio.google.com/)
    2. Sign in with your Google account
    3. Create a new project or select existing
    4. Go to **API Keys** section
    5. Click **Create API Key**
    6. Copy and paste into DIALOGIX

### 3. Set Up Your First Channel

1. Navigate to **Settings** → **Channel Configuration**
2. Select **Add Email Channel** as your first channel
3. Select **Conect whit Google**.
4. Give the permissions.
5. The channel is activate.

### 4. Create Escalation Rules

1. Go to **Settings** → **Escalation Rules**
2. In **Add Rule**
3. Set conditions (e.g., "Domain or user")
4. Define the Escalation Rules (e.g., Foward to email/agent)
5. Click to activate the rule

## ✅ Verification

Verify everything is working:

- [ ] Backend API accessible at `localhost:8000`
- [ ] Frontend UI accessible at `localhost:3000`
- [ ] Can register and login
- [ ] AI configuration is connected
- [ ] At least one channel is active
- [ ] Dashboard shows metrics

## 🎉 You're Ready!

Congratulations! You now have DIALOGIX running locally. Your chatbot is ready to handle conversations.

## Next Steps

<div class="grid cards" markdown>

### ⚙️ Advanced Configuration
Customize your chatbot's personality, add more channels, and fine-tune settings.

### 🔌 API Integration
Integrate with external systems using our comprehensive REST API.
[API Documentation](../api/overview.md)

### 🚀 Production Deployment
Deploy DIALOGIX to production with Docker, cloud services, or bare metal.

### 📊 Analytics Deep Dive
Learn to interpret metrics and optimize your chatbot performance.

</div>

---

!!! question "Need Help?"

    - Check the Troubleshooting Guide
    - Review the FAQ
    - [Contact Support](../support/contact.md)

Happy chatbot building! 🤖
