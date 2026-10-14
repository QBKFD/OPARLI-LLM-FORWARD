#!/bin/bash

# Setup script for cloud server
# Run this on your VM

set -e  # Exit on error

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}🚀 Oracle Cloud Trading Platform Setup${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# Configuration
DB_NAME="oparli_database"
DB_USER="mainuser"
PROJECT_DIR="/home/ubuntu/algo_project"

echo -e "${GREEN}Step 1: Installing PostgreSQL (if needed)...${NC}"
sudo apt update
sudo apt install -y postgresql postgresql-contrib

echo -e "${GREEN}Step 2: Starting PostgreSQL...${NC}"
sudo systemctl start postgresql
sudo systemctl enable postgresql

echo -e "${GREEN}Step 3: Checking if database exists...${NC}"
if sudo -u postgres psql -lqt | cut -d \| -f 1 | grep -qw $DB_NAME; then
    echo -e "${YELLOW}Database $DB_NAME already exists${NC}"
else
    echo -e "${GREEN}Creating database $DB_NAME...${NC}"
    sudo -u postgres createdb $DB_NAME
fi

echo -e "${GREEN}Step 4: Importing database schema...${NC}"
if [ -f "$PROJECT_DIR/database/schema.sql" ]; then
    sudo -u postgres psql -d $DB_NAME < $PROJECT_DIR/database/schema.sql
    echo -e "${GREEN}✅ Schema imported successfully!${NC}"
else
    echo -e "${RED}❌ Schema file not found at $PROJECT_DIR/database/schema.sql${NC}"
    echo -e "${YELLOW}Please upload your project files first${NC}"
    exit 1
fi

echo -e "${GREEN}Step 5: Verifying database setup...${NC}"
sudo -u postgres psql -d $DB_NAME -c "\dt"
echo ""
sudo -u postgres psql -d $DB_NAME -c "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE';"

echo -e "${GREEN}Step 6: Creating database indexes...${NC}"
echo -e "${YELLOW}Indexes already created by schema.sql${NC}"

echo -e "${GREEN}Step 7: Checking Python environment...${NC}"
if [ -d "$PROJECT_DIR/backend/venv" ]; then
    echo -e "${GREEN}✅ Virtual environment exists${NC}"
else
    echo -e "${YELLOW}Creating Python virtual environment...${NC}"
    cd $PROJECT_DIR/backend
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
fi

echo -e "${GREEN}Step 8: Updating backend database connection...${NC}"
cat > $PROJECT_DIR/backend/.env << EOF
DATABASE_URL=postgresql://$DB_USER:$(cat ~/.db_password 2>/dev/null || echo 'your_password_here')@localhost:5432/$DB_NAME

# TWS Connection (configure if using IBKR)
TWS_HOST=127.0.0.1
TWS_PORT=7497
TWS_CLIENT_ID=1
EOF

echo -e "${GREEN}Step 9: Testing database connection...${NC}"
sudo -u postgres psql -d $DB_NAME -c "SELECT COUNT(*) as table_count FROM information_schema.tables WHERE table_schema = 'public';"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ Database Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Database: ${GREEN}$DB_NAME${NC}"
echo -e "User: ${GREEN}$DB_USER${NC}"
echo -e "Tables created: Check output above"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Start FastAPI backend:"
echo "   cd $PROJECT_DIR/backend"
echo "   source venv/bin/activate"
echo "   uvicorn main:app --host 0.0.0.0 --port 8001"
echo ""
echo "2. Test API from your laptop:"
echo "   curl http://your-server-ip:8001/"
echo ""
echo "3. Build and deploy frontend:"
echo "   cd $PROJECT_DIR/frontend"
echo "   npm install && npm run build"
echo ""
echo "4. Setup Nginx to serve frontend"
echo ""
