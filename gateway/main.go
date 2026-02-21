package main

import (
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

func main() {
	cfg := loadConfig()

	dsn := fmt.Sprintf("%s:%s@tcp(%s:%s)/%s?charset=utf8mb4&parseTime=True&loc=Local",
		cfg.DBUser, cfg.DBPassword, cfg.DBHost, cfg.DBPort, cfg.DBName)

	db, err := sql.Open("mysql", dsn)
	if err != nil {
		log.Fatalf("[gateway] failed to open database: %v", err)
	}
	defer db.Close()

	db.SetMaxOpenConns(20)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(5 * time.Minute)

	if err := db.Ping(); err != nil {
		log.Fatalf("[gateway] failed to connect to database: %v", err)
	}
	log.Printf("[gateway] database connected to %s:%s/%s", cfg.DBHost, cfg.DBPort, cfg.DBName)

	handler := newProxyHandler(db)

	addr := ":" + cfg.Port
	log.Printf("[gateway] listening on %s", addr)
	if err := http.ListenAndServe(addr, handler); err != nil {
		log.Fatalf("[gateway] server error: %v", err)
	}
}
