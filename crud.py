from lnbits.db import Database

# LNbits requires every extension to expose a database, even if unused.
db = Database("ext_loop")
