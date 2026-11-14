# Product pricing

The studio reads product base prices from `ansuman_graphic.sqlite3`, in the `products` table. The app inserts the starter catalog on first run and leaves existing prices untouched on later runs.

To change a price, open the SQLite database with a SQLite editor and update the matching product row. For example:

```sql
UPDATE products SET price = 649 WHERE slug = 'tshirt';
```

The size upgrades and printing charge are configured in `SIZES` and `PRINTING_CHARGE` in `app.py`.
