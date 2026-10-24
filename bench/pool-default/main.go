// Prints the connection-pool size pgx gives Dispatch when DATABASE_URL carries no pool_max_conns, next to
// the size it gives with pool_max_conns=10 (what this repository sets). Dispatch calls pgxpool.New(ctx, dsn)
// with the DSN unchanged (cmd/api/main.go), so ParseConfig decides the pool size.
package main

import (
	"fmt"
	"runtime"

	"github.com/jackc/pgx/v5/pgxpool"
)

func main() {
	for _, dsn := range []string{
		"postgres://u:p@db:5432/dispatch",
		"postgres://u:p@db:5432/dispatch?pool_max_conns=10",
	} {
		cfg, err := pgxpool.ParseConfig(dsn)
		if err != nil {
			panic(err)
		}
		fmt.Printf("NumCPU=%d MaxConns=%d dsn_query=%q\n", runtime.NumCPU(), cfg.MaxConns, query(dsn))
	}
}

func query(dsn string) string {
	for i := 0; i < len(dsn); i++ {
		if dsn[i] == '?' {
			return dsn[i+1:]
		}
	}
	return ""
}
