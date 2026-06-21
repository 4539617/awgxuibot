#!/usr/bin/env python3
"""
Database Migration Script for Multi-Panel Support
Adds panel_id column to user_clients table
"""

import sqlite3
import sys
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def migrate_database(db_path: str, default_panel_id: str = "panel1"):
    """
    Migrate database to support multi-panel architecture
    
    Args:
        db_path: Path to the SQLite database
        default_panel_id: Default panel ID for existing records
    """
    logger.info(f"🔄 Starting database migration for: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if panel_id column already exists
        cursor.execute("PRAGMA table_info(user_clients)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'panel_id' in columns:
            logger.warning("⚠️ Column 'panel_id' already exists in user_clients table")
            logger.info("✅ Database is already migrated")
            conn.close()
            return True
        
        logger.info("📋 Adding 'panel_id' column to user_clients table...")
        
        # Add panel_id column
        cursor.execute("""
            ALTER TABLE user_clients 
            ADD COLUMN panel_id TEXT DEFAULT NULL
        """)
        
        logger.info(f"📝 Updating existing records with default panel: {default_panel_id}")
        
        # Update existing records with default panel
        cursor.execute("""
            UPDATE user_clients 
            SET panel_id = ? 
            WHERE panel_id IS NULL
        """, (default_panel_id,))
        
        affected_rows = cursor.rowcount
        logger.info(f"✅ Updated {affected_rows} existing records")
        
        # Create indexes for better performance
        logger.info("🔍 Creating indexes...")
        
        try:
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_clients_panel_id 
                ON user_clients(panel_id)
            """)
            logger.info("✅ Created index: idx_user_clients_panel_id")
        except sqlite3.OperationalError as e:
            logger.warning(f"⚠️ Index already exists or error: {e}")
        
        try:
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_clients_user_panel 
                ON user_clients(user_id, panel_id)
            """)
            logger.info("✅ Created index: idx_user_clients_user_panel")
        except sqlite3.OperationalError as e:
            logger.warning(f"⚠️ Index already exists or error: {e}")
        
        # Commit changes
        conn.commit()
        
        # Verify migration
        cursor.execute("PRAGMA table_info(user_clients)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'panel_id' in columns:
            logger.info("✅ Migration completed successfully!")
            logger.info(f"📊 Database schema updated: {', '.join(columns)}")
        else:
            logger.error("❌ Migration failed: panel_id column not found after migration")
            conn.close()
            return False
        
        conn.close()
        return True
        
    except sqlite3.Error as e:
        logger.error(f"❌ Database error: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        return False


def main():
    """Main migration function"""
    # Default database path
    default_db_path = "/app/data/bot_users.db"
    
    # Check if custom path provided
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    else:
        db_path = default_db_path
    
    # Check if custom default panel provided
    if len(sys.argv) > 2:
        default_panel = sys.argv[2]
    else:
        default_panel = "panel1"
    
    logger.info("=" * 60)
    logger.info("🚀 XUIBot Database Migration Tool")
    logger.info("=" * 60)
    logger.info(f"Database: {db_path}")
    logger.info(f"Default Panel: {default_panel}")
    logger.info("=" * 60)
    
    # Check if database exists
    if not Path(db_path).exists():
        logger.error(f"❌ Database file not found: {db_path}")
        logger.info("💡 Tip: Provide database path as argument:")
        logger.info(f"   python migrate_db.py /path/to/database.db [default_panel_id]")
        sys.exit(1)
    
    # Run migration
    success = migrate_database(db_path, default_panel)
    
    if success:
        logger.info("=" * 60)
        logger.info("✅ Migration completed successfully!")
        logger.info("=" * 60)
        logger.info("📝 Next steps:")
        logger.info("   1. Update config.yaml with your panel configurations")
        logger.info("   2. Restart the bot")
        logger.info("   3. Test multi-panel functionality")
        sys.exit(0)
    else:
        logger.error("=" * 60)
        logger.error("❌ Migration failed!")
        logger.error("=" * 60)
        logger.error("💡 Please check the error messages above")
        logger.error("💡 You may need to restore from backup")
        sys.exit(1)


if __name__ == "__main__":
    main()

# Made with Bob
