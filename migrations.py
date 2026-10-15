from lnbits.db import Connection


async def m001_initial(db: Connection):
    """Loop Out swaps started from LNbits."""
    await db.execute(f"""
        CREATE TABLE loop.swaps (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            amount {db.big_int} NOT NULL,
            address TEXT NOT NULL,
            status TEXT NOT NULL,
            loop_state TEXT,
            failure_reason TEXT,
            swap_hash TEXT UNIQUE,
            htlc_address TEXT,
            server_pubkey TEXT NOT NULL,
            swap_fee {db.big_int} NOT NULL,
            prepay_amount {db.big_int} NOT NULL,
            max_miner_fee {db.big_int} NOT NULL,
            max_swap_routing_fee {db.big_int} NOT NULL,
            max_prepay_routing_fee {db.big_int} NOT NULL,
            swap_checking_id TEXT NOT NULL,
            prepay_checking_id TEXT,
            prepay_hash TEXT,
            payments_recorded BOOLEAN NOT NULL DEFAULT FALSE,
            cost_server {db.big_int} NOT NULL DEFAULT 0,
            cost_onchain {db.big_int} NOT NULL DEFAULT 0,
            cost_offchain {db.big_int} NOT NULL DEFAULT 0,
            done BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT {db.timestamp_column_default},
            updated_at TIMESTAMP DEFAULT {db.timestamp_column_default}
        );
    """)
