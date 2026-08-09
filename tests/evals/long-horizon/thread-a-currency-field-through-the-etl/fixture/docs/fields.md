# Field notes

Kept for the finance team; the authoritative list is `etl/schema.py`.

| field       | kind      | notes                                                 |
|-------------|-----------|-------------------------------------------------------|
| order_id    | str       | shop-local id, unique per run after `dedupe`           |
| customer_id | str       | stable across shops                                    |
| channel     | str       | constrained by the `channels` allowlist                |
| amount      | decimal2  | always two decimal places, no thousands separators      |
| quantity    | int       | positive                                               |
| region      | str       | optional; blank for online-only orders                 |

Source column names differ per shop. The retail export calls the customer column
`cust` and the gross amount `amount_gross`; the reader for that format owns the
mapping.
