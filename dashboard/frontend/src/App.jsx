import React from "react"
import axios from "axios"
import { useState, useEffect, useRef } from "react"

const API = "http://92.4.90.188:8081"
const WS  = "ws://92.4.90.188:8081/ws/updates"

const C = {
  bg:       "#060b14",
  panel:    "#0a1220",
  card:     "#0d1526",
  border:   "#1a2840",
  border2:  "#0f1e35",
  text:     "#c8d8f0",
  muted:    "#3a5070",
  dim:      "#1e3050",
  green:    "#00e87a",
  red:      "#ff3d5a",
  blue:     "#3b82f6",
  orange:   "#f59e0b",
  cyan:     "#06b6d4",
  purple:   "#8b5cf6",
  yellow:   "#eab308",
}

const pnlC  = v => (v || 0) >= 0 ? C.green : C.red
const pnlBg = v => (v || 0) >= 0 ? "rgba(0,232,122,0.07)" : "rgba(255,61,90,0.07)"
const STATE_C = { RUNNING: C.green, STOPPED: C.muted, ERROR: C.red, IDLE: C.blue }
const fmt = (n, d = 2) => n != null ? Number(n).toFixed(d) : "—"
const fmtRs = (n, d = 2) => n != null ? `₹${fmt(n, d)}` : "—"
const fmtTime = s => s ? s.slice(11, 19) : "—"

// ── Calendar Data ─────────────────────────────────────────────────────────────
const CALENDAR = {
  "2026-05-25": { nakshatra: "Rohini", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and low-risk trend-following trades." },
  "2026-05-26": { nakshatra: "Mrigashira", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Market opening hour", advice: "Avoid impulsive entries and aggressive option buying." },
  "2026-05-27": { nakshatra: "Ardra", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best day for structured intraday execution and analysis." },
  "2026-05-28": { nakshatra: "Punarvasu", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better suited for long-term investment decisions." },
  "2026-05-29": { nakshatra: "Pushya", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Strong day for profits, scaling positions, and financial decisions." },
  "2026-06-01": { nakshatra: "Rohini", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-06-02": { nakshatra: "Mrigashira", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-06-03": { nakshatra: "Ardra", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-06-04": { nakshatra: "Punarvasu", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-06-05": { nakshatra: "Pushya", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-06-08": { nakshatra: "Ashlesha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-06-09": { nakshatra: "Magha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-06-10": { nakshatra: "Purva Phalguni", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-06-11": { nakshatra: "Uttara Phalguni", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-06-12": { nakshatra: "Hasta", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-06-15": { nakshatra: "Chitra", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-06-16": { nakshatra: "Swati", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-06-17": { nakshatra: "Vishakha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-06-18": { nakshatra: "Anuradha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-06-19": { nakshatra: "Jyeshtha", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-06-22": { nakshatra: "Moola", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-06-23": { nakshatra: "Purva Ashadha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-06-24": { nakshatra: "Uttara Ashadha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-06-25": { nakshatra: "Shravana", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-06-26": { nakshatra: "Dhanishta", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-06-29": { nakshatra: "Shatabhisha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-06-30": { nakshatra: "Purva Bhadrapada", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-07-01": { nakshatra: "Rohini", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-07-02": { nakshatra: "Mrigashira", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-07-03": { nakshatra: "Ardra", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-07-06": { nakshatra: "Punarvasu", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-07-07": { nakshatra: "Pushya", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-07-08": { nakshatra: "Ashlesha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-07-09": { nakshatra: "Magha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-07-10": { nakshatra: "Purva Phalguni", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-07-13": { nakshatra: "Uttara Phalguni", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-07-14": { nakshatra: "Hasta", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-07-15": { nakshatra: "Chitra", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-07-16": { nakshatra: "Swati", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-07-17": { nakshatra: "Vishakha", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-07-20": { nakshatra: "Anuradha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-07-21": { nakshatra: "Jyeshtha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-07-22": { nakshatra: "Moola", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-07-23": { nakshatra: "Purva Ashadha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-07-24": { nakshatra: "Uttara Ashadha", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-07-27": { nakshatra: "Shravana", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-07-28": { nakshatra: "Dhanishta", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-07-29": { nakshatra: "Shatabhisha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-07-30": { nakshatra: "Purva Bhadrapada", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-07-31": { nakshatra: "Uttara Bhadrapada", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-08-03": { nakshatra: "Rohini", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-08-04": { nakshatra: "Mrigashira", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-08-05": { nakshatra: "Ardra", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-08-06": { nakshatra: "Punarvasu", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-08-07": { nakshatra: "Pushya", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-08-10": { nakshatra: "Ashlesha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-08-11": { nakshatra: "Magha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-08-12": { nakshatra: "Purva Phalguni", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-08-13": { nakshatra: "Uttara Phalguni", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-08-14": { nakshatra: "Hasta", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-08-17": { nakshatra: "Chitra", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-08-18": { nakshatra: "Swati", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-08-19": { nakshatra: "Vishakha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-08-20": { nakshatra: "Anuradha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-08-21": { nakshatra: "Jyeshtha", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-08-24": { nakshatra: "Moola", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-08-25": { nakshatra: "Purva Ashadha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-08-26": { nakshatra: "Uttara Ashadha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-08-27": { nakshatra: "Shravana", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-08-28": { nakshatra: "Dhanishta", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-08-31": { nakshatra: "Shatabhisha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-09-01": { nakshatra: "Rohini", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-09-02": { nakshatra: "Mrigashira", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-09-03": { nakshatra: "Ardra", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-09-04": { nakshatra: "Punarvasu", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-09-07": { nakshatra: "Pushya", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-09-08": { nakshatra: "Ashlesha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-09-09": { nakshatra: "Magha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-09-10": { nakshatra: "Purva Phalguni", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-09-11": { nakshatra: "Uttara Phalguni", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-09-14": { nakshatra: "Hasta", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-09-15": { nakshatra: "Chitra", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-09-16": { nakshatra: "Swati", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-09-17": { nakshatra: "Vishakha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-09-18": { nakshatra: "Anuradha", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-09-21": { nakshatra: "Jyeshtha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-09-22": { nakshatra: "Moola", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-09-23": { nakshatra: "Purva Ashadha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-09-24": { nakshatra: "Uttara Ashadha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-09-25": { nakshatra: "Shravana", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-09-28": { nakshatra: "Dhanishta", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-09-29": { nakshatra: "Shatabhisha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-09-30": { nakshatra: "Purva Bhadrapada", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-10-01": { nakshatra: "Rohini", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-10-02": { nakshatra: "Mrigashira", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-10-05": { nakshatra: "Ardra", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-10-06": { nakshatra: "Punarvasu", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-10-07": { nakshatra: "Pushya", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-10-08": { nakshatra: "Ashlesha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-10-09": { nakshatra: "Magha", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-10-12": { nakshatra: "Purva Phalguni", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-10-13": { nakshatra: "Uttara Phalguni", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-10-14": { nakshatra: "Hasta", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-10-15": { nakshatra: "Chitra", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-10-16": { nakshatra: "Swati", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-10-19": { nakshatra: "Vishakha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-10-20": { nakshatra: "Anuradha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-10-21": { nakshatra: "Jyeshtha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-10-22": { nakshatra: "Moola", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-10-23": { nakshatra: "Purva Ashadha", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-10-26": { nakshatra: "Uttara Ashadha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-10-27": { nakshatra: "Shravana", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-10-28": { nakshatra: "Dhanishta", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-10-29": { nakshatra: "Shatabhisha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-10-30": { nakshatra: "Purva Bhadrapada", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-11-02": { nakshatra: "Rohini", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-11-03": { nakshatra: "Mrigashira", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-11-04": { nakshatra: "Ardra", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-11-05": { nakshatra: "Punarvasu", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-11-06": { nakshatra: "Pushya", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-11-09": { nakshatra: "Ashlesha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-11-10": { nakshatra: "Magha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-11-11": { nakshatra: "Purva Phalguni", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-11-12": { nakshatra: "Uttara Phalguni", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-11-13": { nakshatra: "Hasta", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-11-16": { nakshatra: "Chitra", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-11-17": { nakshatra: "Swati", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-11-18": { nakshatra: "Vishakha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-11-19": { nakshatra: "Anuradha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-11-20": { nakshatra: "Jyeshtha", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-11-23": { nakshatra: "Moola", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-11-24": { nakshatra: "Purva Ashadha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-11-25": { nakshatra: "Uttara Ashadha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-11-26": { nakshatra: "Shravana", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-11-27": { nakshatra: "Dhanishta", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-11-30": { nakshatra: "Shatabhisha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-12-01": { nakshatra: "Rohini", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-12-02": { nakshatra: "Mrigashira", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-12-03": { nakshatra: "Ardra", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-12-04": { nakshatra: "Punarvasu", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-12-07": { nakshatra: "Pushya", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-12-08": { nakshatra: "Ashlesha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-12-09": { nakshatra: "Magha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-12-10": { nakshatra: "Purva Phalguni", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-12-11": { nakshatra: "Uttara Phalguni", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-12-14": { nakshatra: "Hasta", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-12-15": { nakshatra: "Chitra", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-12-16": { nakshatra: "Swati", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-12-17": { nakshatra: "Vishakha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-12-18": { nakshatra: "Anuradha", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-12-21": { nakshatra: "Jyeshtha", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-12-22": { nakshatra: "Moola", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-12-23": { nakshatra: "Purva Ashadha", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-12-24": { nakshatra: "Uttara Ashadha", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
  "2026-12-25": { nakshatra: "Shravana", tara: "Very Strong", rating: "Very Good", ratingCol: "#39d353", fav: "1:00 PM – 3:00 PM", avoid: "3:00 PM – 3:30 PM", advice: "Good for profit booking and scaling positions." },
  "2026-12-28": { nakshatra: "Dhanishta", tara: "Favourable", rating: "Good", ratingCol: "#26a641", fav: "10:00 AM – 11:00 AM", avoid: "12:00 PM – 1:00 PM", advice: "Good for planning and selective low-risk trades." },
  "2026-12-29": { nakshatra: "Shatabhisha", tara: "Moderate", rating: "Volatile", ratingCol: "#ff3d5a", fav: "1:00 PM – 2:10 PM", avoid: "Opening hour", advice: "Avoid impulsive entries and emotional trades." },
  "2026-12-30": { nakshatra: "Purva Bhadrapada", tara: "Strong", rating: "Excellent", ratingCol: "#00e87a", fav: "9:35 AM – 11:10 AM", avoid: "12:00 PM – 1:00 PM", advice: "Best for structured intraday and momentum trades." },
  "2026-12-31": { nakshatra: "Uttara Bhadrapada", tara: "Moderate", rating: "Balanced", ratingCol: "#f59e0b", fav: "10:20 AM – 11:10 AM", avoid: "Thursday noon", advice: "Better for long-term and strategic decisions." },
}

function getTodayCalendar() {
  const ist = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" })
  return CALENDAR[ist] || null
}

// ── Token expiry from JWT ─────────────────────────────────────────────────────
function parseTokenExpiry(token) {
  try {
    const part = token.split(".")[1]
    const pad = part + "=".repeat((4 - part.length % 4) % 4)
    const payload = JSON.parse(atob(pad))
    return payload.exp ? new Date(payload.exp * 1000) : null
  } catch { return null }
}

function useCapital() {
  const [capital, setCapital] = useState({ available: 0, used: 0, total: 0 })
  useEffect(() => {
    async function fetch() {
      try {
        const r = await axios.get(`${API}/api/funds`)
        if (r.data) setCapital(r.data)
      } catch {}
    }
    fetch()
    const id = setInterval(fetch, 30000)
    return () => clearInterval(id)
  }, [])
  return capital
}

function Pill({ label, colour, size = 11 }) {
  return (
    <span style={{
      background: colour + "18", color: colour, borderRadius: 3,
      padding: "2px 8px", fontSize: size, fontWeight: 700, letterSpacing: 0.8,
      border: `1px solid ${colour}30`, whiteSpace: "nowrap",
    }}>{label}</span>
  )
}

function MiniSpark({ history, w = 80, h = 28 }) {
  if (!history || history.length < 2) return <div style={{ width: w, height: h }} />
  const vals = history.map(x => x.pnl)
  const mn = Math.min(...vals), mx = Math.max(...vals), rng = mx - mn || 1
  const pts = vals.map((v, i) => {
    const x = (i / (vals.length - 1)) * w
    const y = h - ((v - mn) / rng) * (h - 4) - 2
    return `${x},${y}`
  }).join(" ")
  const last = vals[vals.length - 1]
  const col = last >= 0 ? C.green : C.red
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <polyline points={pts} fill="none" stroke={col} strokeWidth="1.5" strokeLinejoin="round" opacity="0.8"/>
    </svg>
  )
}

// ── Token Expiry Countdown ────────────────────────────────────────────────────
function TokenCountdown({ token }) {
  const [remaining, setRemaining] = useState(null)
  const expiry = token ? parseTokenExpiry(token) : null

  useEffect(() => {
    if (!expiry) return
    const tick = () => {
      const diff = Math.floor((expiry - new Date()) / 1000)
      setRemaining(diff)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [expiry])

  if (!expiry || remaining === null) return (
    <div style={{ background: C.card, borderRadius: 8, padding: "8px 14px", border: `1px solid ${C.border}`, minWidth: 140 }}>
      <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>TOKEN EXPIRY</div>
      <div style={{ fontSize: 12, color: C.muted, fontFamily: "monospace", marginTop: 2 }}>Unknown</div>
    </div>
  )

  const hours = Math.floor(remaining / 3600)
  const mins  = Math.floor((remaining % 3600) / 60)
  const secs  = remaining % 60
  const col   = remaining < 3600 ? C.red : remaining < 7200 ? C.orange : C.green
  const label = remaining < 0 ? "EXPIRED!" : `${hours}h ${mins}m ${secs}s`

  return (
    <div style={{ background: C.card, borderRadius: 8, padding: "8px 14px", border: `1px solid ${col}40`, minWidth: 140 }}>
      <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 2 }}>TOKEN EXPIRY</div>
      <div style={{ fontSize: 13, fontWeight: 800, color: col, fontFamily: "monospace" }}>{label}</div>
      {remaining < 3600 && remaining > 0 && (
        <div style={{ fontSize: 9, color: C.orange, marginTop: 2 }}>⚠ Run get_token.py soon!</div>
      )}
      {remaining < 0 && (
        <div style={{ fontSize: 9, color: C.red, marginTop: 2 }}>🔴 Bot is blind — refresh token!</div>
      )}
    </div>
  )
}

// ── Auto-Stop Countdown ───────────────────────────────────────────────────────
function AutoStopCountdown() {
  const [remaining, setRemaining] = useState(null)

  useEffect(() => {
    const tick = () => {
      const now = new Date()
      const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }))
      const stop = new Date(ist)
      stop.setHours(15, 10, 0, 0)
      const diff = Math.floor((stop - ist) / 1000)
      setRemaining(diff)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  if (remaining === null) return null

  const hours = Math.floor(Math.abs(remaining) / 3600)
  const mins  = Math.floor((Math.abs(remaining) % 3600) / 60)
  const secs  = Math.abs(remaining) % 60
  const fired = remaining <= 0
  const col   = fired ? C.muted : remaining < 1800 ? C.orange : C.cyan

  return (
    <div style={{ background: C.card, borderRadius: 8, padding: "8px 14px", border: `1px solid ${col}40`, minWidth: 140 }}>
      <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 2 }}>AUTO-STOP (3:10 PM)</div>
      <div style={{ fontSize: 13, fontWeight: 800, color: col, fontFamily: "monospace" }}>
        {fired ? "FIRED ✓" : `${hours}h ${mins}m ${secs}s`}
      </div>
      {!fired && remaining < 1800 && (
        <div style={{ fontSize: 9, color: C.orange, marginTop: 2 }}>⚠ Approaching auto-stop</div>
      )}
    </div>
  )
}

// ── Paper/Live Toggle ─────────────────────────────────────────────────────────
function PaperLiveToggle({ isPaper }) {
  const [localPaper, setLocalPaper] = useState(isPaper)
  const [toggling, setToggling]     = useState(false)
  const [msg, setMsg]               = useState("")

  useEffect(() => { setLocalPaper(isPaper) }, [isPaper])

  const handleToggle = async () => {
    if (toggling) return
    setToggling(true)
    setMsg("")
    try {
      const res = await axios.post(`${API}/api/toggle-paper`)
      if (res.data.success !== false) {
        setLocalPaper(res.data.paper_trade)
        setMsg(res.data.paper_trade ? "✓ Switched to PAPER — bot restarting..." : "⚠ Switched to LIVE — bot restarting...")
      } else {
        setMsg("Failed: " + (res.data.error || "unknown"))
      }
    } catch (e) {
      setMsg("Error: " + e.message)
    }
    setToggling(false)
    setTimeout(() => setMsg(""), 6000)
  }

  const col = localPaper ? C.orange : C.red

  return (
    <div style={{ background: C.card, borderRadius: 8, padding: "8px 14px", border: `2px solid ${col}50`, minWidth: 180 }}>
      <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 6 }}>TRADING MODE</div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 11, color: localPaper ? C.orange : C.muted, fontWeight: localPaper ? 800 : 400 }}>PAPER</span>
        {/* Toggle */}
        <div onClick={handleToggle} style={{
          width: 44, height: 24, borderRadius: 99,
          background: localPaper ? "#f59e0b" : "#ff3d5a",
          position: "relative", cursor: toggling ? "wait" : "pointer",
          border: `1px solid ${col}`,
          transition: "background 0.3s",
          flexShrink: 0,
        }}>
          <div style={{
            width: 18, height: 18, borderRadius: "50%",
            background: "#fff",
            position: "absolute", top: 3,
            left: localPaper ? 3 : 23,
            transition: "left 0.3s",
          }} />
        </div>
        <span style={{ fontSize: 11, color: !localPaper ? C.red : C.muted, fontWeight: !localPaper ? 800 : 400 }}>LIVE</span>
      </div>
      {msg && (
        <div style={{ fontSize: 9, color: msg.includes("⚠") ? C.red : C.green, marginTop: 5 }}>{msg}</div>
      )}
    </div>
  )
}

// ── Astro Calendar Panel ──────────────────────────────────────────────────────
function AstroCalendarPanel() {
  const cal = getTodayCalendar()
  const today = new Date().toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "long", day: "numeric", month: "short" })

  if (!cal) return (
    <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}`, display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>✦ ASTRO TRADING CALENDAR</div>
      <div style={{ fontSize: 12, color: C.muted }}>No calendar data for today ({today})</div>
    </div>
  )

  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `2px solid ${cal.ratingCol}40`, display: "flex", gap: 20, flexWrap: "wrap", alignItems: "flex-start" }}>
      {/* Header */}
      <div style={{ minWidth: 140 }}>
        <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>✦ ASTRO CALENDAR · {today.toUpperCase()}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 18, fontWeight: 800, color: cal.ratingCol }}>{cal.rating.toUpperCase()}</span>
          <span style={{ fontSize: 10, color: cal.ratingCol, background: cal.ratingCol + "20", padding: "2px 8px", borderRadius: 4, border: `1px solid ${cal.ratingCol}40` }}>{cal.tara}</span>
        </div>
        <div style={{ fontSize: 11, color: C.text, marginTop: 4 }}>🌙 {cal.nakshatra}</div>
      </div>

      {/* Divider */}
      <div style={{ width: 1, background: C.border, alignSelf: "stretch" }} />

      {/* Favourable */}
      <div>
        <div style={{ fontSize: 9, color: C.green, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>✓ FAVOURABLE WINDOW</div>
        <div style={{ fontSize: 14, fontWeight: 800, color: C.green, fontFamily: "monospace" }}>{cal.fav}</div>
      </div>

      {/* Avoid */}
      <div>
        <div style={{ fontSize: 9, color: C.red, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>✗ AVOID</div>
        <div style={{ fontSize: 14, fontWeight: 800, color: C.red, fontFamily: "monospace" }}>{cal.avoid}</div>
      </div>

      {/* Divider */}
      <div style={{ width: 1, background: C.border, alignSelf: "stretch" }} />

      {/* Advice */}
      <div style={{ flex: 1, minWidth: 200 }}>
        <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>TODAY'S ADVICE</div>
        <div style={{ fontSize: 12, color: C.text, lineHeight: 1.6, borderLeft: `3px solid ${cal.ratingCol}`, paddingLeft: 10 }}>{cal.advice}</div>
      </div>
    </div>
  )
}

// ── Session Plan Panel ────────────────────────────────────────────────────────
function SessionPlanPanel() {
  const [plan, setPlan] = useState(null)

  useEffect(() => {
    async function fetchPlan() {
      try {
        const r = await axios.get(`${API}/api/session-plan`)
        setPlan(r.data)
      } catch {}
    }
    fetchPlan()
    const id = setInterval(fetchPlan, 30000)
    return () => clearInterval(id)
  }, [])

  if (!plan || !plan.is_ready) return (
    <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}` }}>
      <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 6 }}>SESSION PLAN</div>
      <div style={{ fontSize: 12, color: C.muted }}>Waiting for 9:30 AM session plan...</div>
    </div>
  )

  const regCol = plan.regime === "range" ? C.blue : C.green
  const confCol = plan.confidence === "HIGH" ? C.green : plan.confidence === "MEDIUM" ? C.orange : C.red
  const rs = plan.scores || {}
  const maxScore = Math.max(rs.range_score || 0, rs.trending_score || 0, 1)

  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>SESSION PLAN · {plan.produced_at ? plan.produced_at.slice(11, 16) : "—"}</div>
        <div style={{ display: "flex", gap: 6 }}>
          <span style={{ fontSize: 10, fontWeight: 800, color: regCol, background: regCol + "20", padding: "2px 8px", borderRadius: 4, border: `1px solid ${regCol}40` }}>
            {plan.regime?.toUpperCase()}
          </span>
          <span style={{ fontSize: 10, fontWeight: 800, color: confCol, background: confCol + "20", padding: "2px 8px", borderRadius: 4, border: `1px solid ${confCol}40` }}>
            {plan.confidence}
          </span>
        </div>
      </div>

      {/* Score bars */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
        {[
          { label: "RANGE SCORE", val: rs.range_score || 0, col: C.blue },
          { label: "TREND SCORE", val: rs.trending_score || 0, col: C.green },
        ].map(({ label, val, col }) => (
          <div key={label}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
              <span style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>{label}</span>
              <span style={{ fontSize: 9, color: col, fontWeight: 800 }}>{val}</span>
            </div>
            <div style={{ height: 6, background: C.border, borderRadius: 3 }}>
              <div style={{ height: "100%", width: `${(val / maxScore) * 100}%`, background: col, borderRadius: 3, transition: "width 0.5s" }} />
            </div>
          </div>
        ))}
      </div>

      {/* Key signals */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
        {rs.vix_signal && <span style={{ fontSize: 9, color: C.muted, background: C.panel, padding: "3px 8px", borderRadius: 4, border: `1px solid ${C.border}` }}>VIX: {rs.vix_signal}</span>}
        {rs.or_width_signal && <span style={{ fontSize: 9, color: C.muted, background: C.panel, padding: "3px 8px", borderRadius: 4, border: `1px solid ${C.border}` }}>{rs.or_width_signal}</span>}
        {rs.pcr_signal && <span style={{ fontSize: 9, color: C.muted, background: C.panel, padding: "3px 8px", borderRadius: 4, border: `1px solid ${C.border}` }}>{rs.pcr_signal}</span>}
      </div>

      {/* Strategy gates */}
      <div style={{ display: "flex", gap: 8 }}>
        <div style={{ flex: 1, background: plan.survivor_active ? "#00e87a12" : "#ff3d5a12", borderRadius: 6, padding: "6px 10px", border: `1px solid ${plan.survivor_active ? C.green : C.red}30`, textAlign: "center" }}>
          <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>SURVIVOR</div>
          <div style={{ fontSize: 11, fontWeight: 800, color: plan.survivor_active ? C.green : C.red }}>{plan.survivor_active ? "✅ ACTIVE" : "❌ BLOCKED"}</div>
        </div>
        <div style={{ flex: 1, background: plan.wave_active ? "#00e87a12" : "#ff3d5a12", borderRadius: 6, padding: "6px 10px", border: `1px solid ${plan.wave_active ? C.green : C.red}30`, textAlign: "center" }}>
          <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>WAVE</div>
          <div style={{ fontSize: 11, fontWeight: 800, color: plan.wave_active ? C.green : C.red }}>{plan.wave_active ? "✅ ACTIVE" : "❌ BLOCKED"}</div>
        </div>
        <div style={{ flex: 1, background: C.panel, borderRadius: 6, padding: "6px 10px", border: `1px solid ${C.border}`, textAlign: "center" }}>
          <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>LIMIT</div>
          <div style={{ fontSize: 11, fontWeight: 800, color: C.red }}>{fmtRs(plan.daily_loss_limit)}</div>
        </div>
      </div>
    </div>
  )
}


// ── P&L LINE CHART ────────────────────────────────────────────────────────────
function PnlLineChart({ trades, height = 180 }) {
  const w = 560, h = height
  const pad = { top: 20, right: 50, bottom: 28, left: 58 }
  const iw = w - pad.left - pad.right
  const ih = h - pad.top - pad.bottom

  const todayStr = new Date().toISOString().slice(0, 10)
  const todayTrades = trades
    .filter(t => t.status === "CLOSED" && t.exit_time?.slice(0, 10) === todayStr && t.exit_time)
    .sort((a, b) => new Date(a.exit_time) - new Date(b.exit_time))

  const points = [{ time: "09:15", cumPnl: 0, pnl: 0 }]
  let cumPnl = 0
  todayTrades.forEach(t => {
    cumPnl += (t.realised_pnl || 0)
    points.push({ time: t.exit_time?.slice(11, 16) || "", cumPnl, pnl: t.realised_pnl || 0, strategy: t.strategy })
  })
  if (points.length === 1) {
    const now = new Date().toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false })
    points.push({ time: now, cumPnl: 0, pnl: 0 })
  }

  const minPnl = Math.min(...points.map(p => p.cumPnl), -500)
  const maxPnl = Math.max(...points.map(p => p.cumPnl), 500)
  const range  = maxPnl - minPnl || 1

  const toX = i => pad.left + (i / Math.max(points.length - 1, 1)) * iw
  const toY = v => pad.top + ih - ((v - minPnl) / range) * ih
  const zeroY = toY(0)
  const lastPnl = points[points.length - 1]?.cumPnl || 0
  const lineCol = lastPnl >= 0 ? "#00e87a" : "#ff3d5a"
  const pathD = points.map((p, i) => `${i === 0 ? "M" : "L"} ${toX(i).toFixed(1)} ${toY(p.cumPnl).toFixed(1)}`).join(" ")
  const fillD = `${pathD} L ${toX(points.length-1).toFixed(1)} ${zeroY.toFixed(1)} L ${toX(0).toFixed(1)} ${zeroY.toFixed(1)} Z`
  const yTicks = [-5000, 0, Math.round(maxPnl/2), maxPnl].filter(v => v >= minPnl && v <= maxPnl)

  return (
    <div style={{ width: "100%", overflowX: "auto" }}>
      <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" style={{ display: "block" }}>
        {yTicks.map(v => (
          <g key={v}>
            <line x1={pad.left} y1={toY(v)} x2={pad.left+iw} y2={toY(v)} stroke="#1a2840" strokeWidth={1} strokeDasharray="4 4" />
            <text x={pad.left-4} y={toY(v)+4} textAnchor="end" fill={v === 0 ? "#3a5070" : v < 0 ? "#ff3d5a60" : "#00e87a60"} fontSize={9} fontFamily="monospace">
              {v >= 0 ? `+₹${v}` : `-₹${Math.abs(v)}`}
            </text>
          </g>
        ))}
        <line x1={pad.left} y1={zeroY} x2={pad.left+iw} y2={zeroY} stroke="#3a5070" strokeWidth={1.5} />
        {toY(-5000) >= pad.top && toY(-5000) <= pad.top+ih && (
          <line x1={pad.left} y1={toY(-5000)} x2={pad.left+iw} y2={toY(-5000)} stroke="#ff3d5a" strokeWidth={1} strokeDasharray="6 3" opacity={0.4} />
        )}
        {points.length > 1 && <path d={fillD} fill={lineCol} opacity={0.07} />}
        {points.length > 1 && <path d={pathD} fill="none" stroke={lineCol} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />}
        {points.slice(1).map((p, i) => (
          <circle key={i} cx={toX(i+1)} cy={toY(p.cumPnl)} r={4} fill={p.pnl >= 0 ? "#00e87a" : "#ff3d5a"} stroke="#060b14" strokeWidth={1.5}>
            <title>{p.time} | {p.strategy} | ₹{p.pnl?.toFixed(0)} | Total: ₹{p.cumPnl?.toFixed(0)}</title>
          </circle>
        ))}
        {points.length > 1 && (
          <text x={toX(points.length-1)+6} y={toY(lastPnl)+4} fill={lineCol} fontSize={11} fontWeight="800" fontFamily="monospace">
            {lastPnl >= 0 ? "+" : ""}₹{lastPnl.toFixed(0)}
          </text>
        )}
        {points.length <= 1 && (
          <text x={w/2} y={h/2} textAnchor="middle" fill="#3a5070" fontSize={12} fontFamily="monospace">No closed trades today</text>
        )}
        {[points[0], points[Math.floor(points.length/2)], points[points.length-1]].filter(Boolean).map((p, i, arr) => {
          const idx = points.indexOf(p)
          return <text key={i} x={toX(idx)} y={pad.top+ih+16} textAnchor="middle" fill="#3a5070" fontSize={8} fontFamily="monospace">{p.time}</text>
        })}
      </svg>
    </div>
  )
}


// ── SOUND ALERTS ─────────────────────────────────────────────────────────────
function playTone(freq, duration, type = "sine", vol = 0.3) {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const osc  = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.connect(gain); gain.connect(ctx.destination)
    osc.type = type
    osc.frequency.setValueAtTime(freq, ctx.currentTime)
    gain.gain.setValueAtTime(vol, ctx.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration)
    osc.start(ctx.currentTime); osc.stop(ctx.currentTime + duration)
  } catch {}
}

function playTradeWin()  { playTone(523,0.12); setTimeout(()=>playTone(659,0.12),130); setTimeout(()=>playTone(784,0.2),260) }
function playTradeLoss() { playTone(400,0.2,"sawtooth",0.2); setTimeout(()=>playTone(280,0.3,"sawtooth",0.2),220) }
function playLossAlarm() { for(let i=0;i<3;i++){setTimeout(()=>playTone(880,0.18,"square",0.35),i*320); setTimeout(()=>playTone(440,0.18,"square",0.35),i*320+160)} }

function SoundControl({ enabled, onToggle }) {
  return (
    <button onClick={onToggle} style={{
      background: enabled ? "#00e87a18" : "transparent",
      border: `1px solid ${enabled ? "#00e87a40" : "#1a2840"}`,
      borderRadius: 6, padding: "4px 10px",
      color: enabled ? "#00e87a" : "#3a5070",
      fontSize: 11, fontWeight: 700, cursor: "pointer", fontFamily: "monospace",
    }}>{enabled ? "🔊 SND" : "🔇 SND"}</button>
  )
}

function CapitalBar({ trades, global: g, capital: capData }) {
  const total = capData?.total || 200000
  const used = trades.filter(t => t.status === "OPEN").reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0), 0)
  const available = capData?.available ?? Math.max(0, total - used)
  const pct = Math.min(100, (used / total) * 100)
  const barC = pct > 80 ? C.red : pct > 50 ? C.orange : C.green
  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}`, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1.5 }}>CAPITAL OVERVIEW</span>
        <span style={{ fontSize: 10, color: barC, fontWeight: 700 }}>{pct.toFixed(1)}% USED</span>
      </div>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {[
          { label: "TOTAL",      val: fmtRs(total),     col: C.text },
          { label: "AVAILABLE",  val: fmtRs(available), col: C.green },
          { label: "IN TRADES",  val: fmtRs(used),      col: used > 0 ? C.orange : C.muted },
          { label: "FREE MARGIN", val: fmtRs(available), col: C.cyan },
        ].map(({ label, val, col }) => (
          <div key={label}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{label}</div>
            <div style={{ fontSize: 13, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>
      <div style={{ height: 4, background: C.border, borderRadius: 2, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: barC, borderRadius: 2, transition: "width 0.5s" }} />
      </div>
    </div>
  )
}

function NiftyBox({ market }) {
  const [prev, setPrev] = useState(0)
  const [flash, setFlash] = useState(null)
  const price = market?.nifty_price || 0
  useEffect(() => {
    if (prev && price && price !== prev) {
      setFlash(price > prev ? "up" : "down")
      setTimeout(() => setFlash(null), 500)
    }
    setPrev(price)
  }, [price])
  const col = flash === "up" ? C.green : flash === "down" ? C.red : C.text
  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "12px 16px", border: `1px solid ${flash ? (flash === "up" ? C.green : C.red) : C.border}`, minWidth: 160, transition: "border-color 0.3s" }}>
      <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1.5, marginBottom: 3 }}>NIFTY 50</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: col, fontFamily: "monospace", transition: "color 0.3s" }}>
        {price > 0 ? price.toFixed(2) : "—"}{flash === "up" ? " ▲" : flash === "down" ? " ▼" : ""}
      </div>
      {market?.option_price > 0 && (
        <div style={{ fontSize: 10, color: C.muted, marginTop: 2 }}>OPT: <span style={{ color: C.text }}>₹{market.option_price.toFixed(2)}</span></div>
      )}
    </div>
  )
}

function StatTile({ label, value, colour, sub, bg }) {
  return (
    <div style={{ background: bg || C.card, borderRadius: 10, padding: "12px 16px", border: `1px solid ${C.border}`, minWidth: 100 }}>
      <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1.5, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 800, color: colour || C.text, fontFamily: "monospace" }}>{value}</div>
      {sub && <div style={{ fontSize: 9, color: C.muted, marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function VixBox({ vix }) {
  if (!vix) return null
  const v = vix.value
  const col = v >= 25 ? C.red : v >= 20 ? C.orange : v >= 16 ? C.yellow : C.green
  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "12px 16px", border: `1px solid ${col}30`, minWidth: 120 }}>
      <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1.5, marginBottom: 3 }}>INDIA VIX</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontSize: 22, fontWeight: 800, color: col, fontFamily: "monospace" }}>{v != null ? v.toFixed(2) : "—"}</span>
        <span style={{ fontSize: 9, color: col, fontWeight: 700 }}>{vix.regime}</span>
      </div>
    </div>
  )
}

function StratCard({ name, data, onStop, onReset, trades, stratCapital }) {
  const title = name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())
  const myTrades = trades.filter(t => t.strategy === name)
  const openTrades = myTrades.filter(t => t.status === "OPEN")
  const closedTrades = myTrades.filter(t => t.status === "CLOSED")
  const winCount = closedTrades.filter(t => (t.realised_pnl || 0) > 0).length
  const winRate = closedTrades.length > 0 ? ((winCount / closedTrades.length) * 100).toFixed(0) : "—"
  // Capital numbers now come from risk_manager via /api/capital -- the same
  // source of truth used by the actual capital guard that blocks trades,
  // instead of this card's own disconnected local calc (see 31-Jul
  // capital-tracking investigation). saviour_combo's BankNifty trades
  // register capital under the "bn_survivor" strategy name internally.
  const stratKey = name === "saviour_combo" ? "bn_survivor" : name
  const capSlice = stratCapital?.strategies?.find(x => x.key === stratKey)
  const capitalUsed  = capSlice ? capSlice.deployed : 0
  const capitalLimit = capSlice ? capSlice.cap : 40000
  const capPct = Math.min(100, capitalLimit > 0 ? (capitalUsed / capitalLimit) * 100 : 0)
  const capCol = capPct > 80 ? C.red : capPct > 50 ? C.orange : C.green
  const capDrift     = capSlice?.drift ?? 0
  const capDriftFlag = capSlice?.drift_flag ?? false

  if (!data) return (
    <div style={{ background: C.card, borderRadius: 12, padding: 18, border: `1px solid ${C.border}`, minHeight: 180, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <span style={{ color: C.muted, fontSize: 12 }}>{title} — waiting...</span>
    </div>
  )

  const realised   = data.realised_pnl   || 0
  const unrealised = data.unrealised_pnl || 0
  const net        = realised + unrealised
  const col        = STATE_C[data.state] || C.muted
  const capEff     = capitalUsed > 0 ? ((net / capitalUsed) * 100).toFixed(1) : "—"

  return (
    <div style={{ background: C.card, borderRadius: 12, padding: 18, border: `1px solid ${col}25`, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontWeight: 800, fontSize: 14, color: C.text }}>{title}</span>
        <Pill label={data.state || "IDLE"} colour={col} />
      </div>
      <div style={{ background: pnlBg(net), borderRadius: 8, padding: "10px 14px", border: `1px solid ${pnlC(net)}20`, display: "flex", gap: 16, alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>NET P&L</div>
          <div style={{ fontSize: 22, fontWeight: 800, color: pnlC(net), fontFamily: "monospace" }}>{fmtRs(net)}</div>
        </div>
        <div style={{ borderLeft: `1px solid ${C.border}`, paddingLeft: 16, display: "flex", gap: 14 }}>
          <div>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>REALISED</div>
            <div style={{ fontSize: 13, fontWeight: 700, color: pnlC(realised), fontFamily: "monospace" }}>{fmtRs(realised)}</div>
          </div>
          <div>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>UNREALISED</div>
            <div style={{ fontSize: 13, fontWeight: 700, color: pnlC(unrealised), fontFamily: "monospace" }}>{fmtRs(unrealised)}</div>
          </div>
        </div>
        <div style={{ marginLeft: "auto" }}><MiniSpark history={data.pnl_history} /></div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
        {[
          { label: "POSITION",    val: data.position || "FLAT" },
          { label: "OPEN TRADES", val: data.open_trades || 0 },
          { label: "TOTAL",       val: data.total_trades || 0 },
          { label: "WIN RATE",    val: winRate !== "—" ? `${winRate}%` : "—" },
        ].map(({ label, val }) => (
          <div key={label} style={{ background: C.panel, borderRadius: 6, padding: "7px 9px", border: `1px solid ${C.border2}` }}>
            <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 0.8 }}>{label}</div>
            <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginTop: 2 }}>{val}</div>
          </div>
        ))}
      </div>
      <div style={{ background: C.panel, borderRadius: 8, padding: "10px 12px", border: `1px solid ${C.border2}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
          <span style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>CAPITAL ALLOCATION</span>
          <span style={{ fontSize: 9, color: capCol, fontWeight: 700 }}>{capPct.toFixed(0)}% OF {fmtRs(capitalLimit)}</span>
        </div>
        <div style={{ display: "flex", gap: 12, marginBottom: 6 }}>
          <div><div style={{ fontSize: 8, color: C.muted }}>USED</div><div style={{ fontSize: 11, fontWeight: 700, color: capCol, fontFamily: "monospace" }}>{fmtRs(capitalUsed)}</div></div>
          <div><div style={{ fontSize: 8, color: C.muted }}>REMAINING</div><div style={{ fontSize: 11, fontWeight: 700, color: C.green, fontFamily: "monospace" }}>{fmtRs(capitalLimit - capitalUsed)}</div></div>
          <div><div style={{ fontSize: 8, color: C.muted }}>EFFICIENCY</div><div style={{ fontSize: 11, fontWeight: 700, color: C.cyan, fontFamily: "monospace" }}>{capEff !== "—" ? `${capEff}%` : "—"}</div></div>
        </div>
        <div style={{ height: 3, background: C.border, borderRadius: 2 }}>
          <div style={{ height: "100%", width: `${capPct}%`, background: capCol, borderRadius: 2, transition: "width 0.5s" }} />
        </div>
        {capDriftFlag && (
          <div style={{ marginTop: 6, fontSize: 9, color: C.red, fontWeight: 700 }}>
            ⚠ DRIFT ₹{fmt(Math.abs(capDrift), 0)} vs ground truth — auto-heals within 5min
          </div>
        )}
      </div>
      <div style={{ background: C.panel, borderRadius: 6, padding: "7px 10px", fontSize: 10, color: "#4a8080", borderLeft: `2px solid ${C.dim}`, fontFamily: "monospace", minHeight: 26 }}>
        {data.last_signal || "— no signal yet —"}
      </div>
      {data.error_message && (
        <div style={{ color: C.red, fontSize: 10, background: "#ff3d5a10", borderRadius: 6, padding: "6px 10px" }}>⚠ {data.error_message}</div>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={() => onStop(name)} disabled={data.state !== "RUNNING"}
          style={{ flex: 1, padding: "7px 0", borderRadius: 6, border: data.state === "RUNNING" ? `1px solid ${C.red}40` : `1px solid ${C.border}`, background: data.state === "RUNNING" ? "#ff3d5a18" : C.border, color: data.state === "RUNNING" ? C.red : C.muted, fontWeight: 700, cursor: data.state === "RUNNING" ? "pointer" : "not-allowed", fontSize: 11, fontFamily: "monospace" }}>STOP</button>
        <button onClick={() => onReset(name)} disabled={data.state !== "ERROR"}
          style={{ flex: 1, padding: "7px 0", borderRadius: 6, border: data.state === "ERROR" ? `1px solid ${C.orange}40` : `1px solid ${C.border}`, background: data.state === "ERROR" ? "#f59e0b18" : C.border, color: data.state === "ERROR" ? C.orange : C.muted, fontWeight: 700, cursor: data.state === "ERROR" ? "pointer" : "not-allowed", fontSize: 11, fontFamily: "monospace" }}>RESET</button>
      </div>
    </div>
  )
}

function TradeLedger({ trades }) {
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState("ALL")
  const filtered = trades.filter(t => filter === "ALL" ? true : t.status === filter)
  const cols = ["TRADE ID", "STRATEGY", "INSTRUMENT", "DIR", "ENTRY TIME", "ENTRY ₹", "EXIT TIME", "EXIT ₹", "PREMIUM", "% RETURN", "EXIT REASON", "STATUS", "P&L"]

  const exportCSV = () => {
    const rows = [cols.join(",")]
    trades.forEach(t => {
      const premium = (t.entry_price || 0) * (t.quantity || 0)
      rows.push([
        t.id, t.strategy, t.symbol, t.order_type,
        t.entry_time, t.entry_price, t.exit_time, t.exit_price,
        premium.toFixed(2), premium > 0 ? ((t.realised_pnl||0)/premium*100).toFixed(1)+"%" : "0%", t.notes||"", t.status, t.realised_pnl
      ].join(","))
    })
    const blob = new Blob([rows.join("\n")], { type: "text/csv" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `trades_${new Date().toISOString().slice(0,10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (selected) {
    const t = selected
    const premium = (t.entry_price || 0) * (t.quantity || 0)
    const margin = premium * 5
    const maxRisk = premium * 0.35
    const rr = maxRisk > 0 ? ((t.realised_pnl || 0) / maxRisk).toFixed(2) : "—"
    return (
      <div>
        <button onClick={() => setSelected(null)} style={{ background: C.panel, border: `1px solid ${C.border}`, color: C.cyan, padding: "6px 14px", borderRadius: 6, cursor: "pointer", fontSize: 11, fontFamily: "monospace", marginBottom: 16 }}>← BACK TO LEDGER</button>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {[
            { title: "TRADE DETAILS", rows: [["Trade ID", t.id], ["Strategy", t.strategy], ["Instrument", t.symbol], ["Direction", t.order_type], ["Quantity", t.quantity], ["Status", t.status], ["Broker Order ID", t.broker_order_id]] },
            { title: "CAPITAL & RISK", rows: [["Entry Price", fmtRs(t.entry_price)], ["Exit Price", fmtRs(t.exit_price)], ["Premium", fmtRs(premium)], ["Est. Margin", fmtRs(margin)], ["Max Risk", fmtRs(maxRisk)], ["Realised P&L", fmtRs(t.realised_pnl)], ["% Return", premium > 0 ? ((t.realised_pnl||0)/premium*100).toFixed(1)+"%" : "—"], ["Risk/Reward", rr]] },
          ].map(({ title, rows }) => (
            <div key={title} style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
              <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>{title}</div>
              {rows.map(([label, val]) => (
                <div key={label} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: `1px solid ${C.border2}` }}>
                  <span style={{ fontSize: 11, color: C.muted }}>{label}</span>
                  <span style={{ fontSize: 11, color: C.text, fontFamily: "monospace" }}>{val || "—"}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 12, justifyContent: "space-between", flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 8 }}>
          {["ALL", "OPEN", "CLOSED", "CANCELLED"].map(f => (
            <button key={f} onClick={() => setFilter(f)} style={{ padding: "5px 12px", borderRadius: 5, border: `1px solid ${filter === f ? C.cyan : C.border}`, background: filter === f ? C.cyan + "20" : "transparent", color: filter === f ? C.cyan : C.muted, fontSize: 10, fontWeight: 700, cursor: "pointer", fontFamily: "monospace" }}>
              {f} ({trades.filter(t => f === "ALL" ? true : t.status === f).length})
            </button>
          ))}
        </div>
        <button onClick={exportCSV} style={{ padding: "5px 14px", borderRadius: 5, border: `1px solid ${C.green}40`, background: C.green + "15", color: C.green, fontSize: 10, fontWeight: 700, cursor: "pointer", fontFamily: "monospace" }}>
          ↓ EXPORT CSV
        </button>
      </div>
      {filtered.length === 0 ? (
        <div style={{ color: C.muted, textAlign: "center", padding: "28px 0", fontSize: 12 }}>No trades found</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr style={{ background: C.panel }}>
                {cols.map(h => <th key={h} style={{ padding: "9px 12px", textAlign: "left", fontWeight: 700, color: C.muted, fontSize: 9, letterSpacing: 1, borderBottom: `1px solid ${C.border}`, whiteSpace: "nowrap" }}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {filtered.map((t, i) => {
                const premium = (t.entry_price || 0) * (t.quantity || 0)
                const statusCol = t.status === "OPEN" ? C.blue : t.status === "CLOSED" ? C.green : C.muted
                return (
                  <tr key={t.id || i} onClick={() => setSelected(t)} style={{ borderBottom: `1px solid ${C.border2}`, cursor: "pointer" }}
                    onMouseEnter={e => e.currentTarget.style.background = C.panel}
                    onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                    <td style={{ padding: "8px 12px", color: C.cyan, fontFamily: "monospace", fontSize: 10 }}>{(t.id || "").slice(0, 8)}…</td>
                    <td style={{ padding: "8px 12px", color: C.text }}>{t.strategy}</td>
                    <td style={{ padding: "8px 12px", color: C.text, fontFamily: "monospace", fontSize: 10 }}>{t.symbol}</td>
                    <td style={{ padding: "8px 12px", fontWeight: 700, color: t.order_type === "SELL" ? C.red : C.green }}>{t.order_type}</td>

                    <td style={{ padding: "8px 12px", color: C.muted, fontFamily: "monospace", fontSize: 10 }}>{fmtTime(t.entry_time)}</td>
                    <td style={{ padding: "8px 12px", color: C.text, fontFamily: "monospace" }}>{fmtRs(t.entry_price)}</td>
                    <td style={{ padding: "8px 12px", color: C.muted, fontFamily: "monospace", fontSize: 10 }}>{fmtTime(t.exit_time)}</td>
                    <td style={{ padding: "8px 12px", color: C.text, fontFamily: "monospace" }}>{fmtRs(t.exit_price)}</td>
                    <td style={{ padding: "8px 12px", color: C.orange, fontFamily: "monospace" }}>{fmtRs(premium)}</td>
                    <td style={{ padding: "8px 12px", color: C.muted, fontFamily: "monospace", fontSize: 10, whiteSpace: "nowrap" }}>{t.notes ? t.notes.replace(/_/g," ") : "—"}</td>
                    <td style={{ padding: "8px 12px", color: pnlC(t.realised_pnl), fontFamily: "monospace", fontWeight: 700 }}>{premium > 0 ? ((t.realised_pnl||0)/premium*100).toFixed(1)+"%" : "—"}</td>
                    <td style={{ padding: "8px 12px" }}><Pill label={t.status} colour={statusCol} size={9} /></td>
                    <td style={{ padding: "8px 12px", fontWeight: 700, color: pnlC(t.realised_pnl), fontFamily: "monospace" }}>{fmtRs(t.realised_pnl)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function BankNiftyPanel() {
  const [trades,  setTrades]  = React.useState([])
  const [summary, setSummary] = React.useState(null)
  const [opp,     setOpp]     = React.useState(null)
  const [cap,     setCap]     = React.useState(null)

  React.useEffect(() => {
    function fetch() {
      axios.get(`${API}/api/banknifty/trades?limit=100`).then(r => setTrades(r.data.trades || [])).catch(() => {})
      axios.get(`${API}/api/banknifty/summary`).then(r => setSummary(r.data)).catch(() => {})
      axios.get(`${API}/api/opportunities`).then(r => {
        const bn = (r.data.strategies || []).find(s => s.strategy === bn_survivor)
        setOpp(bn || null)
      }).catch(() => {})
      axios.get(`${API}/api/capital`).then(r => {
        const bn = (r.data.strategies || []).find(s => s.key === bn_survivor)
        setCap(bn || null)
      }).catch(() => {})
    }
    fetch()
    const id = setInterval(fetch, 5000)
    return () => clearInterval(id)
  }, [])

  const open   = trades.filter(t => t.status === "OPEN")
  const closed = trades.filter(t => t.status === "CLOSED")
  const totalPnl = closed.reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const wins   = closed.filter(t => (t.realised_pnl || 0) > 0).length

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

      {/* Header */}
      <div style={{ background: "#1a1a2e", border: "1px solid #4a4a8a40", borderRadius: 10, padding: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div>
            <div style={{ fontSize: 14, fontWeight: 800, color: "#a78bfa" }}>📄 BANKNIFTY PAPER TRADING</div>
            <div style={{ fontSize: 10, color: C.muted, marginTop: 2 }}>Paper mode — zero real capital risk</div>
          </div>
          <div style={{ background: "#f59e0b20", border: "1px solid #f59e0b40", borderRadius: 6, padding: "4px 12px", fontSize: 11, fontWeight: 700, color: "#f59e0b" }}>PAPER ONLY</div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px,1fr))", gap: 10 }}>
          {[
            { label: "TOTAL TRADES",  val: closed.length,          col: C.text },
            { label: "OPEN NOW",      val: open.length,            col: open.length > 0 ? C.blue : C.muted },
            { label: "WIN RATE",      val: closed.length > 0 ? `${((wins/closed.length)*100).toFixed(0)}%` : "–", col: wins/closed.length >= 0.5 ? C.green : C.red },
            { label: "PAPER P&L",     val: fmtRs(totalPnl),       col: pnlC(totalPnl) },
          ].map(({label, val, col}) => (
            <div key={label} style={{ background: C.card, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
              <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
              <div style={{ fontSize: 16, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Regime + Capital + Block Reasons */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div style={{ background: C.panel, borderRadius: 10, padding: 14, border: "1px solid #4a4a8a40" }}>
          <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 8 }}>REGIME / BLOCK REASONS</div>
          {opp ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {(opp.top_reasons || []).slice(0, 3).map((r, i) => (
                <div key={i} style={{ background: C.card, borderRadius: 6, padding: "6px 10px", border: "1px solid #ef444420" }}>
                  <div style={{ fontSize: 10, color: "#ef4444", fontWeight: 600 }}>{r.reason}</div>
                  <div style={{ fontSize: 9, color: C.muted, marginTop: 2 }}>{r.count}x blocked</div>
                </div>
              ))}
              {(!opp.top_reasons || opp.top_reasons.length === 0) && (
                <div style={{ fontSize: 12, color: C.green, fontWeight: 700 }}>No blocks — regime compatible</div>
              )}
              <div style={{ display: "flex", gap: 12, marginTop: 4 }}>
                <div style={{ fontSize: 10, color: C.muted }}>Det: <span style={{ color: C.text, fontWeight: 700 }}>{opp.detected}</span></div>
                <div style={{ fontSize: 10, color: C.muted }}>Blk: <span style={{ color: "#ef4444", fontWeight: 700 }}>{opp.blocked}</span></div>
                <div style={{ fontSize: 10, color: C.muted }}>Hit: <span style={{ color: C.green, fontWeight: 700 }}>{(opp.hit_rate * 100).toFixed(1)}%</span></div>
              </div>
            </div>
          ) : <div style={{ color: C.muted, fontSize: 11, textAlign: "center", padding: "8px 0" }}>Warming up...</div>}
        </div>
        <div style={{ background: C.panel, borderRadius: 10, padding: 14, border: "1px solid #4a4a8a40" }}>
          <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 8 }}>CAPITAL POOL</div>
          {cap ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <div style={{ fontSize: 11, color: C.muted }}>Allocated</div>
                <div style={{ fontSize: 13, fontWeight: 800, color: C.text, fontFamily: "monospace" }}>{"\u20b9"}{(cap.cap/1000).toFixed(0)}k</div>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <div style={{ fontSize: 11, color: C.muted }}>Deployed</div>
                <div style={{ fontSize: 13, fontWeight: 800, color: cap.deployed > 0 ? C.blue : C.muted, fontFamily: "monospace" }}>{"\u20b9"}{(cap.deployed/1000).toFixed(0)}k</div>
              </div>
              <div style={{ background: "#ffffff10", borderRadius: 4, height: 6, overflow: "hidden" }}>
                <div style={{ width: `${Math.min(cap.pct, 100)}%`, height: "100%", background: cap.pct > 80 ? "#ef4444" : C.blue, borderRadius: 4 }} />
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <div style={{ fontSize: 11, color: C.muted }}>Free</div>
                <div style={{ fontSize: 13, fontWeight: 800, color: C.green, fontFamily: "monospace" }}>{"\u20b9"}{(cap.free/1000).toFixed(0)}k</div>
              </div>
              <div style={{ background: cap.status === "HEALTHY" ? "#22c55e20" : "#ef444420", borderRadius: 6, padding: "4px 10px", textAlign: "center" }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: cap.status === "HEALTHY" ? C.green : "#ef4444" }}>{cap.status}</div>
              </div>
            </div>
          ) : <div style={{ color: C.muted, fontSize: 11, textAlign: "center", padding: "8px 0" }}>Warming up...</div>}
        </div>
      </div>

      {/* Open Positions */}
      {open.length > 0 && (
        <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 10 }}>OPEN PAPER POSITIONS</div>
          {open.map((t, i) => {
            const unreal = t.unrealised_pnl || 0
            return (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 12px", background: C.card, borderRadius: 6, border: `1px solid ${pnlC(unreal)}20`, marginBottom: 6 }}>
                <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                  <div style={{ fontSize: 10, color: C.muted }}>PAPER</div>
                  <div style={{ fontSize: 12, color: C.text }}>{t.symbol}</div>
                  <div style={{ fontSize: 11, color: t.order_type === "SELL" ? C.red : C.green, fontWeight: 700 }}>{t.order_type}</div>
                  <div style={{ fontSize: 11, color: C.muted }}>entry ₹{t.entry_price}</div>
                  {t.current_ltp > 0 && <div style={{ fontSize: 11, color: C.cyan }}>ltp ₹{t.current_ltp?.toFixed(2)}</div>}
                </div>
                <div style={{ fontSize: 16, fontWeight: 800, color: pnlC(unreal), fontFamily: "monospace" }}>{fmtRs(unreal)}</div>
              </div>
            )
          })}
        </div>
      )}

      {/* Trade History */}
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 10 }}>PAPER TRADE HISTORY</div>
        {closed.length === 0
          ? <div style={{ color: C.muted, textAlign: "center", padding: "16px 0", fontSize: 12 }}>No paper trades yet</div>
          : <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 300, overflowY: "auto" }}>
              {[...closed].reverse().map((t, i) => (
                <div key={i} style={{ display: "flex", gap: 10, padding: "7px 10px", background: C.card, borderRadius: 6, border: `1px solid ${C.border}`, fontSize: 11, alignItems: "center" }}>
                  <div style={{ color: C.muted, fontSize: 10, whiteSpace: "nowrap" }}>{fmtTime(t.exit_time)}</div>
                  <div style={{ color: C.text, flex: 1 }}>{t.symbol}</div>
                  <div style={{ color: t.order_type === "SELL" ? C.red : C.green, fontWeight: 700 }}>{t.order_type}</div>
                  <div style={{ color: C.muted }}>₹{t.entry_price} → ₹{t.exit_price}</div>
                  <div style={{ color: pnlC(t.realised_pnl), fontFamily: "monospace", fontWeight: 700 }}>{fmtRs(t.realised_pnl)}</div>
                  <div style={{ fontSize: 9, color: C.muted }}>{t.notes}</div>
                </div>
              ))}
            </div>
        }
      </div>
    </div>
  )
}

function AlertsPanel() {
  const [alerts, setAlerts] = React.useState([])
  React.useEffect(() => {
    function fetch() {
      axios.get(`${API}/api/alerts`).then(r => setAlerts(r.data.alerts || [])).catch(() => {})
    }
    fetch()
    const id = setInterval(fetch, 3000)
    return () => clearInterval(id)
  }, [])

  const levelColor = l => l === "🚨" ? C.red : l === "⚠️" ? C.orange : l === "✅" ? C.green : l === "🔴" ? C.red : C.blue
  const levelBg   = l => l === "🚨" ? "#ff3d5a15" : l === "⚠️" ? "#f59e0b15" : l === "✅" ? "#00e87a10" : "#1a2840"

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>SYSTEM ALERTS ({alerts.length})</div>
        {alerts.length > 0 && (
          <button onClick={() => axios.post(`${API}/api/alerts/clear`).then(() => setAlerts([]))}
            style={{ fontSize: 9, color: C.muted, background: "none", border: `1px solid ${C.border}`, borderRadius: 4, padding: "2px 8px", cursor: "pointer" }}>
            CLEAR
          </button>
        )}
      </div>
      {alerts.length === 0
        ? <div style={{ color: C.muted, textAlign: "center", padding: "16px 0", fontSize: 12 }}>✅ No alerts — system healthy</div>
        : alerts.map((a, i) => (
          <div key={i} style={{ display: "flex", gap: 10, padding: "8px 12px", background: levelBg(a.level), borderRadius: 6, border: `1px solid ${levelColor(a.level)}30`, alignItems: "flex-start" }}>
            <div style={{ fontSize: 14 }}>{a.level}</div>
            <div style={{ fontSize: 10, color: C.muted, whiteSpace: "nowrap", marginTop: 2, fontFamily: "monospace" }}>{a.time}</div>
            <div style={{ fontSize: 11, color: C.text, fontFamily: "monospace", flex: 1 }}>{a.message}</div>
          </div>
        ))
      }
    </div>
  )
}

function ExecutionLog({ trades }) {
  const logs = []
  trades.forEach(t => {
    if (t.entry_time) logs.push({ time: t.entry_time, type: "ENTRY", msg: `${t.order_type} ${t.symbol} @ ₹${fmt(t.entry_price)} (Qty ${t.quantity})`, strategy: t.strategy, col: t.order_type === "SELL" ? C.red : C.green })
    if (t.exit_time)  logs.push({ time: t.exit_time,  type: "EXIT",  msg: `EXIT ${t.symbol} @ ₹${fmt(t.exit_price)} (P&L: ${fmtRs(t.realised_pnl)})`, strategy: t.strategy, col: pnlC(t.realised_pnl) })
  })
  logs.sort((a, b) => new Date(b.time) - new Date(a.time))
  if (logs.length === 0) return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <AlertsPanel />
      <div style={{ color: C.muted, textAlign: "center", padding: "28px 0", fontSize: 12 }}>No execution history yet</div>
    </div>
  )
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <AlertsPanel />
      <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>EXECUTION HISTORY</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 400, overflowY: "auto" }}>
        {logs.map((log, i) => (
          <div key={i} style={{ display: "flex", gap: 12, padding: "8px 12px", background: C.panel, borderRadius: 6, border: `1px solid ${C.border2}`, alignItems: "flex-start" }}>
            <div style={{ fontSize: 10, color: C.muted, fontFamily: "monospace", whiteSpace: "nowrap", marginTop: 1 }}>{fmtTime(log.time)}</div>
            <Pill label={log.type} colour={log.col} size={9} />
            <div style={{ fontSize: 11, color: C.text, fontFamily: "monospace", flex: 1 }}>{log.msg}</div>
            <div style={{ fontSize: 10, color: C.muted }}>{log.strategy}</div>
          </div>
        ))}
      </div>
    </div>
  )
}


function AnalyticsPanel() {
  const [data, setData] = React.useState(null)
  const [period, setPeriod] = React.useState("daily")
  const [mode, setMode] = React.useState("live") // live or paper

  React.useEffect(() => {
    axios.get(`${API}/api/trades/analytics`).then(r => setData(r.data)).catch(() => {})
    const id = setInterval(() => {
      axios.get(`${API}/api/trades/analytics`).then(r => setData(r.data)).catch(() => {})
    }, 60000)
    return () => clearInterval(id)
  }, [])

  if (!data) return null

  const periodData = data[period] || []
  const maxAbs = Math.max(...periodData.map(d => Math.abs(d.pnl || 0)), 1)

  const liveStrategies  = data.strategy_breakdown.filter(s => s.paper_trade === 0)
  const paperStrategies = data.strategy_breakdown.filter(s => s.paper_trade === 1)
  const strategies = mode === "live" ? liveStrategies : paperStrategies

  const periodLabel = { daily: "DAY", weekly: "WEEK", monthly: "MONTH" }
  const periodKey   = { daily: "day", weekly: "week", monthly: "month" }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, position: "relative", zIndex: 10 }}>

      {/* Strategy Breakdown */}
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>STRATEGY BREAKDOWN</div>
          <div style={{ display: "flex", gap: 6 }}>
            {["live","paper"].map(m => (
              <button type="button" key={m} onClick={() => setMode(m)} style={{
                fontSize: 9, fontWeight: 700, padding: "3px 10px", borderRadius: 4, cursor: "pointer", letterSpacing: 1,
                background: mode === m ? C.cyan + "30" : "transparent",
                color: mode === m ? C.cyan : C.muted,
                border: `1px solid ${mode === m ? C.cyan : C.border}`
              }}>{m.toUpperCase()}</button>
            ))}
          </div>
        </div>
        {strategies.length === 0 ? (
          <div style={{ color: C.muted, textAlign: "center", padding: "12px 0", fontSize: 12 }}>No {mode} trades yet</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {strategies.map(s => {
              const wr = s.total > 0 ? ((s.winners / s.total) * 100).toFixed(0) : 0
              return (
                <div key={s.strategy + s.paper_trade} style={{ background: C.card, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: C.cyan, letterSpacing: 1 }}>{s.strategy.toUpperCase()}</div>
                    <div style={{ fontSize: 14, fontWeight: 800, color: pnlC(s.total_pnl), fontFamily: "monospace" }}>{fmtRs(s.total_pnl)}</div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 6 }}>
                    {[
                      { l: "TRADES", v: s.total,    c: C.text },
                      { l: "WIN %",  v: `${wr}%`,   c: Number(wr) >= 50 ? C.green : C.red },
                      { l: "AVG",    v: fmtRs(s.avg_pnl), c: pnlC(s.avg_pnl) },
                      { l: "BEST",   v: fmtRs(s.best),    c: C.green },
                      { l: "WORST",  v: fmtRs(s.worst),   c: C.red },
                    ].map(({ l, v, c }) => (
                      <div key={l}>
                        <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{l}</div>
                        <div style={{ fontSize: 11, fontWeight: 700, color: c, fontFamily: "monospace" }}>{v}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Period P&L Chart */}
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>P&L BY {periodLabel[period]}</div>
          <div style={{ display: "flex", gap: 6, position: "relative", zIndex: 20 }}>
            {["daily","weekly","monthly"].map(p => (
              <button type="button" key={p} onClick={() => setPeriod(p)} style={{
                fontSize: 9, fontWeight: 700, padding: "3px 10px", borderRadius: 4, cursor: "pointer", letterSpacing: 1,
                background: period === p ? C.accent + "30" : "transparent",
                color: period === p ? C.accent : C.muted,
                border: `1px solid ${period === p ? C.accent : C.border}`
              }}>{p.toUpperCase()}</button>
            ))}
          </div>
        </div>
        {periodData.length === 0 ? (
          <div style={{ color: C.muted, textAlign: "center", padding: "16px 0", fontSize: 12 }}>No live trades in this period</div>
        ) : (
          <>
            <div style={{ display: "flex", gap: 3, alignItems: "flex-end", height: 120, marginBottom: 8, overflow: "hidden" }}>
              {periodData.map((d, i) => {
                const pnlVal = period === "daily" ? (d.pnl || 0) : (d.pnl || 0)
                const h = Math.max(4, (Math.abs(pnlVal) / maxAbs) * 100)
                const col = pnlVal >= 0 ? C.green : C.red
                const label = period === "daily"
                  ? (d.exit_time ? d.exit_time.slice(5,10) : "")
                  : period === "weekly"
                  ? (d.day ? d.day.slice(5) : "")
                  : (d.week ? d.week.replace("2026-W","W") : "")
                const tooltip = period === "daily"
                  ? `${d.symbol || ""} ${d.order_type || ""}`
                  : ""
                return (
                  <div key={i} title={tooltip} style={{ flex: 1, minWidth: 8, display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
                    <div style={{ fontSize: 7, color: col, fontWeight: 700, fontFamily: "monospace", whiteSpace: "nowrap" }}>{pnlVal >= 0 ? "+" : ""}{pnlVal.toFixed(0)}</div>
                    <div style={{ width: "100%", height: h, background: col + "60", borderRadius: 2, border: `1px solid ${col}80` }} />
                    <div style={{ fontSize: 6, color: C.muted, whiteSpace: "nowrap" }}>{label}</div>
                  </div>
                )
              })}
            </div>
            <div style={{ display: "flex", gap: 16, justifyContent: "center", marginTop: 4 }}>
              {[
                { l: "TOTAL P&L", v: fmtRs(periodData.reduce((s,d) => s+(d.pnl||0), 0)), c: pnlC(periodData.reduce((s,d) => s+(d.pnl||0), 0)) },
                { l: period === "daily" ? "TRADES" : "TRADING DAYS",
                  v: period === "daily" ? periodData.length : periodData.reduce((s,d) => s+(d.trades||0), 0),
                  c: C.text },
                { l: period === "daily" ? "WINNERS" : "WIN DAYS",
                  v: period === "daily"
                    ? `${periodData.filter(d => (d.pnl||0) > 0).length}/${periodData.length}`
                    : `${periodData.filter(d => (d.pnl||0) > 0).length}/${periodData.length}`,
                  c: C.cyan },
              ].map(({ l, v, c }) => (
                <div key={l} style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{l}</div>
                  <div style={{ fontSize: 13, fontWeight: 800, color: c, fontFamily: "monospace" }}>{v}</div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function PerformancePanel({ trades }) {
  const closed = trades.filter(t => t.status === "CLOSED")
  const [perf, setPerf] = useState(null)
  useEffect(() => {
    axios.get(`${API}/api/trades/performance`).then(r => setPerf(r.data)).catch(() => {})
    const id = setInterval(() => {
      axios.get(`${API}/api/trades/performance`).then(r => setPerf(r.data)).catch(() => {})
    }, 30000)
    return () => clearInterval(id)
  }, [])
  const wins   = closed.filter(t => (t.realised_pnl || 0) > 0)
  const losses = closed.filter(t => (t.realised_pnl || 0) < 0)
  const totalPnl = closed.reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const avgWin   = wins.length   > 0 ? wins.reduce((s, t) => s + t.realised_pnl, 0) / wins.length : 0
  const avgLoss  = losses.length > 0 ? Math.abs(losses.reduce((s, t) => s + t.realised_pnl, 0) / losses.length) : 0
  const winRate  = closed.length > 0 ? (wins.length / closed.length * 100).toFixed(1) : 0
  const profitFactor = avgLoss > 0 ? (avgWin * wins.length / (avgLoss * losses.length)).toFixed(2) : "—"
  const byDate = {}
  closed.forEach(t => { if (!t.exit_time) return; const d = t.exit_time.slice(0, 10); byDate[d] = (byDate[d] || 0) + (t.realised_pnl || 0) })
  const days = Object.entries(byDate).sort((a, b) => a[0].localeCompare(b[0])).slice(-14)
  const maxAbs = Math.max(...days.map(d => Math.abs(d[1])), 1)
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

      {/* ── Net P&L & Charges Panel ── */}
      {perf && (
        <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
          <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>NET P&L AFTER CHARGES (ALL TIME)</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px,1fr))", gap: 10, marginBottom: 14 }}>
            {[
              { label: "GROSS P&L",     val: `${fmtRs(perf.gross_pnl)} (${perf.total_margin > 0 ? ((perf.gross_pnl/perf.total_margin)*100).toFixed(2) : 0}%)`, col: pnlC(perf.gross_pnl) },
              { label: "TOTAL CHARGES", val: `−₹${perf.total_charges} (${perf.gross_pnl > 0 ? ((perf.total_charges/perf.gross_pnl)*100).toFixed(1) : 0}%)`, col: C.red },
              { label: "NET P&L",       val: `${fmtRs(perf.net_pnl)} (${perf.total_margin > 0 ? ((perf.net_pnl/perf.total_margin)*100).toFixed(2) : 0}%)`, col: pnlC(perf.net_pnl) },
              { label: "MARGIN USED",   val: fmtRs(perf.total_margin),  col: C.cyan },
              { label: "ROI ON MARGIN", val: `${perf.roi_on_margin}%`, col: perf.roi_on_margin >= 0 ? C.green : C.red },
              { label: "TOTAL TRADES",  val: perf.trade_count,          col: C.text },
            ].map(({ label, val, col }) => (
              <div key={label} style={{ background: C.card, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
                <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 15, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 8 }}>CHARGES BREAKDOWN</div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {Object.entries(perf.charges_breakdown).map(([key, val]) => (
              <div key={key} style={{ background: C.card, borderRadius: 6, padding: "6px 12px", border: `1px solid ${C.border}` }}>
                <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{key.toUpperCase().replace("_", " ")}</div>
                <div style={{ fontSize: 12, fontWeight: 700, color: C.red, fontFamily: "monospace" }}>−₹{val}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10 }}>
        {[
          { label: "TOTAL TRADES",   val: closed.length,    col: C.text },
          { label: "WIN RATE",       val: `${winRate}%`,    col: Number(winRate) >= 50 ? C.green : C.red },
          { label: "AVG WIN",        val: fmtRs(avgWin),    col: C.green },
          { label: "AVG LOSS",       val: fmtRs(-avgLoss),  col: C.red },
          { label: "PROFIT FACTOR",  val: profitFactor,     col: Number(profitFactor) >= 1 ? C.green : C.red },
          { label: "TOTAL P&L",      val: fmtRs(totalPnl),  col: pnlC(totalPnl) },
        ].map(({ label, val, col }) => (
          <div key={label} style={{ background: C.panel, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 16, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 10 }}>TODAY'S CUMULATIVE P&L</div>
        <PnlLineChart trades={trades} height={180} />
      </div>

      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>DAILY P&L (LAST 14 DAYS)</div>
        {days.length === 0 ? <div style={{ color: C.muted, textAlign: "center", padding: "16px 0", fontSize: 12 }}>No data yet</div> : (
          <div style={{ display: "flex", gap: 6, alignItems: "flex-end", height: 80 }}>
            {days.map(([date, pnl]) => {
              const h = Math.max(4, (Math.abs(pnl) / maxAbs) * 68)
              const col = pnl >= 0 ? C.green : C.red
              return (
                <div key={date} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                  <div style={{ fontSize: 9, color: col, fontWeight: 700, fontFamily: "monospace" }}>{pnl >= 0 ? "+" : ""}{pnl.toFixed(0)}</div>
                  <div style={{ width: "100%", height: h, background: col + "50", borderRadius: 3, border: `1px solid ${col}60` }} />
                  <div style={{ fontSize: 8, color: C.muted }}>{date.slice(5).replace("-", "/")}</div>
                </div>
              )
            })}
          </div>
        )}
      </div>
      <AnalyticsPanel />
    </div>
  )
}

function OpenPositions({ trades }) {
  const open = trades.filter(t => t.status === "OPEN")
  const [brokerPos, setBrokerPos] = React.useState([])
  const [brokerPnl, setBrokerPnl] = React.useState(null)
  const [mismatch, setMismatch]   = React.useState(false)

  React.useEffect(() => {
    function fetchBroker() {
      axios.get(`${API}/api/broker-positions`).then(r => {
        const pos = r.data.positions || []
        setBrokerPos(pos)
        setBrokerPnl(r.data.total_pnl ?? null)
        // Check mismatch — bot open count vs broker position count
        const botOpen = trades.filter(t => t.status === "OPEN").length
        setMismatch(pos.length > 0 && botOpen !== pos.length)
      }).catch(() => {})
    }
    fetchBroker()
    const id = setInterval(fetchBroker, 5000)
    return () => clearInterval(id)
  }, [trades])
  if (open.length === 0 && brokerPos.length === 0) return <div style={{ color: C.muted, textAlign: "center", padding: "28px 0", fontSize: 12 }}>No open positions</div>
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>

      {/* ── Broker Sync Panel ── */}
      {brokerPos.length > 0 && (
        <div style={{ background: mismatch ? "#ff3d5a15" : "#00e87a10", border: `1px solid ${mismatch ? C.red : C.green}30`, borderRadius: 10, padding: "10px 16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: mismatch ? C.red : C.green, letterSpacing: 1 }}>
              {mismatch ? "⚠ POSITION MISMATCH — BOT vs UPSTOX" : "✅ UPSTOX BROKER POSITIONS"}
            </div>
            <div style={{ fontSize: 12, fontWeight: 800, color: brokerPnl >= 0 ? C.green : C.red, fontFamily: "monospace" }}>
              {brokerPnl !== null ? fmtRs(brokerPnl) : "–"}
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {brokerPos.map((p, i) => (
              <div key={i} style={{ display: "flex", gap: 16, alignItems: "center", fontSize: 12 }}>
                <span style={{ color: C.muted, fontSize: 10 }}>{p.symbol}</span>
                <span style={{ color: p.quantity < 0 ? C.red : C.green }}>{p.quantity > 0 ? "+" : ""}{p.quantity}</span>
                <span style={{ color: C.text }}>avg ₹{p.average_price?.toFixed(2)}</span>
                <span style={{ color: C.muted }}>ltp ₹{p.ltp?.toFixed(2)}</span>
                <span style={{ color: p.pnl >= 0 ? C.green : C.red, fontFamily: "monospace", fontWeight: 700 }}>{fmtRs(p.pnl)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {open.map((t, i) => {
        const unreal  = t.unrealised_pnl || 0
        const ltp     = t.current_ltp || 0
        const fresh   = t.ltp_fresh === true
        const premium = (t.entry_price || 0) * (t.quantity || 0)
        const trailingArmed = t.trailing_armed === true
        const costOfTrade   = t.cost_of_trade || 0
        const peakPnl       = t.peak_pnl || 0
        const trailingFloor = t.trailing_floor || 0
        const capDeployed = 40000
        const pctReturn = premium > 0 ? ((unreal / premium) * 100).toFixed(1) : "0.0"
        const tpTarget  = (premium * 0.40).toFixed(0)
        const pctToTp   = premium > 0 ? Math.min(100, (unreal / (premium * 0.40)) * 100).toFixed(0) : 0
        return (
          <div key={t.id || i} style={{ background: C.panel, borderRadius: 10, padding: "12px 16px", border: `1px solid ${pnlC(unreal)}30`, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 14, alignItems: "center", flexWrap: "wrap" }}>
              {[
                ["STRATEGY", t.strategy],
                ["SYMBOL",   t.symbol],
                ["DIR",      t.order_type],
                ["QTY",      t.quantity],
                ["ENTRY ₹",  fmtRs(t.entry_price)],
                ["LTP ₹",    ltp > 0 ? fmtRs(ltp) : "–"],
                ["TIME",     fmtTime(t.entry_time)],
                ["PREMIUM",  fmtRs(premium)],
                ["CAP USED",  fmtRs(capDeployed)],
                ["% RETURN",  pctReturn + "%"],
                ["TP TARGET", fmtRs(Number(tpTarget))],
                ["COST",      fmtRs(costOfTrade)],
                ["PEAK",      fmtRs(peakPnl)],
              ].map(([label, val]) => (
                <div key={label}>
                  <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>{label}</div>
                  <div style={{ fontSize: 12, fontFamily: label === "LTP ₹" ? "monospace" : "inherit",
                    color: label === "DIR" ? (t.order_type === "SELL" ? C.red : C.green)
                         : label === "LTP ₹" ? (ltp > (t.entry_price||0) ? C.red : C.green)
                         : C.text,
                    fontWeight: label === "DIR" ? 700 : 400 }}>{val}</div>
                </div>
              ))}
              {trailingArmed && <div style={{ background: "#00e87a20", border: "1px solid #00e87a40", borderRadius: 6, padding: "2px 8px", fontSize: 10, color: C.green, fontWeight: 700 }}>🔒 TRAILING ARMED | Floor: {fmtRs(trailingFloor)}</div>}
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, justifyContent: "flex-end", marginBottom: 2 }}>
                <div style={{ width: 6, height: 6, borderRadius: "50%", background: fresh ? C.green : "#888", flexShrink: 0 }} title={fresh ? "LTP live" : "LTP stale — REST fallback active"} />
                <div style={{ fontSize: 9, color: C.muted, fontWeight: 700 }}>UNREALISED P&L</div>
              </div>
              <div style={{ fontSize: 20, fontWeight: 800, color: pnlC(unreal), fontFamily: "monospace" }}>{fmtRs(unreal)}</div>
              {!fresh && <div style={{ fontSize: 9, color: "#888", marginTop: 2 }}>⏳ updating...</div>}
            </div>
          </div>
        )
      })}
    </div>
  )
}



// ── Opportunity Meter ─────────────────────────────────────────────────────────
function OpportunityMeter() {
  const [data, setData] = React.useState(null)
  React.useEffect(() => {
    async function fetch() {
      try {
        const r = await axios.get(`${API}/api/opportunities`)
        setData(r.data)
      } catch {}
    }
    fetch()
    const id = setInterval(fetch, 5000)
    return () => clearInterval(id)
  }, [])

  if (!data) return <div style={{ color: C.muted, fontSize: 11 }}>Loading opportunity data...</div>

  const stratName = { survivor: "Nifty", bn_survivor: "BankNifty", wave_extractor: "Wave" }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1.5 }}>OPPORTUNITY METER</div>

      {/* ── Total Summary ── */}
      <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}`, display: "flex", gap: 24, flexWrap: "wrap" }}>
        {[
          { label: "DETECTED",  val: data.total_detected,  col: C.cyan },
          { label: "EXECUTED",  val: data.total_executed,  col: C.green },
          { label: "BLOCKED",   val: data.total_blocked,   col: C.red },
          { label: "HIT RATE",  val: data.total_hit_rate + "%", col: data.total_hit_rate > 50 ? C.green : C.orange },
        ].map(({ label, val, col }) => (
          <div key={label}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{label}</div>
            <div style={{ fontSize: 20, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>

      {/* ── Per Strategy ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
        {data.strategies.map(s => (
          <div key={s.strategy} style={{ background: C.card, borderRadius: 10, padding: "14px 16px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 10 }}>{stratName[s.strategy] || s.strategy}</div>
            <div style={{ display: "flex", gap: 14, marginBottom: 10, flexWrap: "wrap" }}>
              {[
                { label: "DETECTED", val: s.detected, col: C.cyan },
                { label: "EXECUTED", val: s.executed, col: C.green },
                { label: "HIT RATE", val: s.hit_rate + "%", col: s.hit_rate > 50 ? C.green : C.orange },
              ].map(({ label, val, col }) => (
                <div key={label}>
                  <div style={{ fontSize: 9, color: C.muted }}>{label}</div>
                  <div style={{ fontSize: 13, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
                </div>
              ))}
            </div>
            {/* Execution bar */}
            <div style={{ marginBottom: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                <span style={{ fontSize: 9, color: C.muted }}>EXECUTION RATE</span>
                <span style={{ fontSize: 9, color: C.green }}>{s.hit_rate}%</span>
              </div>
              <div style={{ height: 4, background: C.border, borderRadius: 2 }}>
                <div style={{ height: "100%", width: `${s.hit_rate}%`, background: C.green, borderRadius: 2, transition: "width 0.5s" }} />
              </div>
            </div>
            {/* Top block reasons */}
            {s.top_reasons.length > 0 && (
              <div>
                <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>TOP BLOCK REASONS</div>
                {s.top_reasons.map((r, i) => (
                  <div key={i} style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                    <span style={{ fontSize: 9, color: C.muted, maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.reason}</span>
                    <span style={{ fontSize: 9, color: C.orange, fontFamily: "monospace" }}>{r.count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}


// ── Capital Intelligence Panel ────────────────────────────────────────────────
function AICapitalAdvisor() {
  const [rec, setRec] = React.useState(null)
  const [applying, setApplying] = React.useState(false)
  const [msg, setMsg] = React.useState(null)

  React.useEffect(() => {
    function load() {
      axios.get(`${API}/api/capital/recommendation`).then(r => setRec(r.data)).catch(() => {})
    }
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [])

  async function applyRec() {
    if (!rec) return
    setApplying(true)
    try {
      await axios.post(`${API}/api/capital/configure`, { per_strategy_cap: rec.recommended_cap })
      setMsg(`Applied ₹${(rec.recommended_cap/1000).toFixed(0)}k — restart bot to activate`)
    } catch { setMsg('Failed to apply') }
    setApplying(false)
    setTimeout(() => setMsg(null), 5000)
  }

  const actionCol = { HOLD: C.green, REDUCE: "#ef4444", INCREASE: C.blue }
  const confCol   = { HIGH: C.green, MEDIUM: "#f59e0b", LOW: "#ef4444" }

  return (
    <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: "1px solid #a78bfa30" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 800, color: "#a78bfa" }}>🤖 AI CAPITAL ADVISOR</div>
          <div style={{ fontSize: 10, color: C.muted, marginTop: 2 }}>Regime-aware capital recommendation — review before applying</div>
        </div>
        {rec && <div style={{ background: `${actionCol[rec.action]}20`, border: `1px solid ${actionCol[rec.action]}40`, borderRadius: 6, padding: "4px 12px", fontSize: 11, fontWeight: 700, color: actionCol[rec.action] }}>{rec.action}</div>}
      </div>

      {!rec ? <div style={{ color: C.muted, fontSize: 11 }}>Loading...</div> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* Current vs Recommended */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
            {[
              { label: "CURRENT", val: `₹${(rec.current_cap/1000).toFixed(0)}k`, col: C.text },
              { label: "RECOMMENDED", val: `₹${(rec.recommended_cap/1000).toFixed(0)}k`, col: actionCol[rec.action] },
              { label: "CONFIDENCE", val: rec.confidence, col: confCol[rec.confidence] },
            ].map(({label, val, col}) => (
              <div key={label} style={{ background: C.card, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}`, textAlign: "center" }}>
                <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
                <div style={{ fontSize: 16, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
              </div>
            ))}
          </div>

          {/* Multiplier breakdown */}
          <div style={{ background: C.card, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 8 }}>MULTIPLIER BREAKDOWN</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8 }}>
              {[
                { label: "REGIME", val: rec.multipliers.regime },
                { label: "VIX", val: rec.multipliers.vix },
                { label: "P&L", val: rec.multipliers.pnl },
                { label: "WIN RATE", val: rec.multipliers.win_rate },
              ].map(({label, val}) => (
                <div key={label} style={{ textAlign: "center" }}>
                  <div style={{ fontSize: 9, color: C.muted, marginBottom: 2 }}>{label}</div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: val >= 1 ? C.green : val >= 0.7 ? "#f59e0b" : "#ef4444", fontFamily: "monospace" }}>{(val * 100).toFixed(0)}%</div>
                </div>
              ))}
            </div>
          </div>

          {/* Reasons */}
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {(rec.reasons || []).map((r, i) => (
              <div key={i} style={{ fontSize: 10, color: C.muted, padding: "4px 8px", background: C.card, borderRadius: 4, border: `1px solid ${C.border}` }}>💡 {r}</div>
            ))}
          </div>

          {/* Apply button */}
          {msg ? (
            <div style={{ fontSize: 11, color: C.green, textAlign: "center", padding: "8px", background: "#22c55e15", borderRadius: 6 }}>{msg}</div>
          ) : (
            <button onClick={applyRec} disabled={applying || rec.action === "HOLD"}
              style={{ background: rec.action === "HOLD" ? C.card : `${actionCol[rec.action]}20`, border: `1px solid ${actionCol[rec.action]}40`, borderRadius: 6, padding: "8px 16px", color: rec.action === "HOLD" ? C.muted : actionCol[rec.action], fontWeight: 700, fontSize: 11, cursor: rec.action === "HOLD" ? "default" : "pointer" }}>
              {applying ? "Applying..." : rec.action === "HOLD" ? "✓ Already Optimal" : `Apply ₹${(rec.recommended_cap/1000).toFixed(0)}k Recommendation`}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function CapitalIntelligencePanel({ trades }) {
  const [capData, setCapData] = React.useState(null)
  const [showConfig, setShowConfig] = React.useState(false)
  const [newCap, setNewCap] = React.useState(150000)
  const [preview, setPreview] = React.useState(null)

  React.useEffect(() => {
    async function fetch() {
      try {
        const r = await axios.get(`${API}/api/capital`)
        setCapData(r.data)
        setNewCap(r.data.per_strategy_cap)
      } catch {}
    }
    fetch()
    const id = setInterval(fetch, 5000)
    return () => clearInterval(id)
  }, [])

  function calcPreview(cap) {
    const lots = Math.floor(cap / 40000)
    const risk = lots * 800
    return { lots, risk, daily: risk * 3, cap }
  }

  function handleCapChange(val) {
    setNewCap(val)
    setPreview(calcPreview(val))
  }

  const statusCol = { HEALTHY: C.green, ACTIVE: C.orange, FULL: C.red }

  if (!capData) return (
    <div style={{ background: C.card, borderRadius: 10, padding: 20, border: `1px solid ${C.border}`, color: C.muted, fontSize: 11 }}>
      Loading capital data...
    </div>
  )

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1.5 }}>CAPITAL INTELLIGENCE</span>
        <button onClick={() => setShowConfig(p => !p)}
          style={{ background: showConfig ? C.orange : C.card, color: showConfig ? "#000" : C.text, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 12px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
          {showConfig ? "✕ CLOSE" : "⚙ CONFIGURE"}
        </button>
      </div>
      <div style={{ background: C.card, borderRadius: 10, padding: "14px 18px", border: `1px solid ${C.border}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>TOTAL PORTFOLIO</span>
          <span style={{ fontSize: 10, color: capData.total_pct > 80 ? C.red : capData.total_pct > 50 ? C.orange : C.green, fontWeight: 700 }}>{capData.total_pct}% DEPLOYED</span>
        </div>
        <div style={{ display: "flex", gap: 20, marginBottom: 10, flexWrap: "wrap" }}>
          {[
            { label: "TOTAL CAP",  val: fmtRs(capData.total_cap),     col: C.text },
            { label: "DEPLOYED",   val: fmtRs(capData.total_deployed), col: C.orange },
            { label: "FREE",       val: fmtRs(capData.total_free),     col: C.green },
          ].map(({ label, val, col }) => (
            <div key={label}>
              <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{label}</div>
              <div style={{ fontSize: 14, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
            </div>
          ))}
        </div>
        <div style={{ height: 6, background: C.border, borderRadius: 3 }}>
          <div style={{ height: "100%", width: `${capData.total_pct}%`, background: capData.total_pct > 80 ? C.red : capData.total_pct > 50 ? C.orange : C.green, borderRadius: 3, transition: "width 0.5s" }} />
        </div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 }}>
        {capData.strategies.map(s => (
          <div key={s.key} style={{ background: C.card, borderRadius: 10, padding: "14px 16px", border: `1px solid ${(statusCol[s.status] || C.border)}40` }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
              <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{s.name.toUpperCase()}</span>
              <span style={{ fontSize: 9, color: statusCol[s.status], fontWeight: 700, background: `${statusCol[s.status]}20`, padding: "2px 6px", borderRadius: 4 }}>{s.status}</span>
            </div>
            <div style={{ display: "flex", gap: 12, marginBottom: 8, flexWrap: "wrap" }}>
              <div><div style={{ fontSize: 9, color: C.muted }}>DEPLOYED</div><div style={{ fontSize: 13, fontWeight: 800, color: C.orange, fontFamily: "monospace" }}>{fmtRs(s.deployed)}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted }}>FREE</div><div style={{ fontSize: 13, fontWeight: 800, color: C.green, fontFamily: "monospace" }}>{fmtRs(s.free)}</div></div>
              <div><div style={{ fontSize: 9, color: C.muted }}>LOTS</div><div style={{ fontSize: 13, fontWeight: 800, color: C.cyan, fontFamily: "monospace" }}>{s.current_lots}/{s.max_lots}</div></div>
            </div>
            <div style={{ height: 4, background: C.border, borderRadius: 2 }}>
              <div style={{ height: "100%", width: `${s.pct}%`, background: statusCol[s.status], borderRadius: 2, transition: "width 0.5s" }} />
            </div>
            <div style={{ fontSize: 9, color: C.muted, marginTop: 4 }}>{s.pct}% of {fmtRs(s.cap)}</div>
          </div>
        ))}
      </div>
      {showConfig && (
        <div style={{ background: C.card, borderRadius: 10, padding: "16px 18px", border: `1px solid ${C.orange}40` }}>
          <div style={{ fontSize: 10, color: C.orange, fontWeight: 700, letterSpacing: 1, marginBottom: 12 }}>⚙ CAPITAL CONFIGURATION</div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
            <span style={{ fontSize: 10, color: C.muted }}>Per-Strategy Cap:</span>
            <input type="range" min={50000} max={500000} step={10000} value={newCap}
              onChange={e => handleCapChange(Number(e.target.value))}
              style={{ width: 200, accentColor: C.orange }} />
            <span style={{ fontSize: 13, fontWeight: 800, color: C.orange, fontFamily: "monospace" }}>{fmtRs(newCap)}</span>
          </div>
          <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap" }}>
            {[100000, 150000, 200000, 250000, 300000].map(v => (
              <button key={v} onClick={() => handleCapChange(v)}
                style={{ background: newCap === v ? C.orange : C.panel, color: newCap === v ? "#000" : C.text, border: `1px solid ${C.border}`, borderRadius: 6, padding: "4px 10px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
                {fmtRs(v)}
              </button>
            ))}
          </div>
          {preview && (
            <div style={{ background: C.panel, borderRadius: 8, padding: "10px 14px", marginBottom: 12, border: `1px solid ${C.border}` }}>
              <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 8 }}>LIVE PREVIEW</div>
              <div style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
                {[
                  { label: "MAX LOTS",       val: preview.lots },
                  { label: "MAX RISK/TRADE", val: fmtRs(preview.risk) },
                  { label: "MAX DAILY RISK", val: fmtRs(preview.daily) },
                  { label: "NEW CAP",        val: fmtRs(preview.cap) },
                ].map(({ label, val }) => (
                  <div key={label}>
                    <div style={{ fontSize: 9, color: C.muted }}>{label}</div>
                    <div style={{ fontSize: 12, fontWeight: 800, color: C.orange, fontFamily: "monospace" }}>{val}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => { setShowConfig(false); setPreview(null) }}
              style={{ background: C.panel, color: C.muted, border: `1px solid ${C.border}`, borderRadius: 6, padding: "6px 14px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
              CANCEL
            </button>
            <button onClick={async () => {
                try {
                  await axios.post(`${API}/api/capital/configure`, { per_strategy_cap: newCap })
                  alert(`Capital updated to ${fmtRs(newCap)} — restart bot to apply`)
                  setShowConfig(false)
                } catch { alert("Failed to update capital") }
              }}
              style={{ background: C.orange, color: "#000", border: "none", borderRadius: 6, padding: "6px 14px", fontSize: 10, fontWeight: 700, cursor: "pointer" }}>
              APPLY
            </button>
          </div>
        </div>
      )}
    </div>
  )
}


function RiskPanel({ trades, global: g }) {
  const open = trades.filter(t => t.status === "OPEN")
  const maxDailyLoss = 5000
  const todayStr = new Date().toISOString().slice(0, 10)
  const todayPnl = trades.filter(t => t.status === "CLOSED" && t.exit_time?.slice(0, 10) === todayStr).reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const ddPct = Math.min(100, (Math.abs(Math.min(0, todayPnl)) / maxDailyLoss) * 100)
  const ddCol = ddPct > 80 ? C.red : ddPct > 50 ? C.orange : C.green
  const totalMarginUsed = open.reduce((s, t) => s + (t.entry_price || 0) * (t.quantity || 0) * 5, 0)
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
        {[
          { label: "TODAY P&L",       val: fmtRs(todayPnl),       col: pnlC(todayPnl) },
          { label: "EST MARGIN LOCKED", val: fmtRs(totalMarginUsed), col: C.cyan },
          { label: "DAILY LOSS LIMIT", val: fmtRs(maxDailyLoss),   col: C.muted },
          { label: "OPEN POSITIONS",  val: open.length,             col: open.length > 2 ? C.orange : C.green },
        ].map(({ label, val, col }) => (
          <div key={label} style={{ background: C.panel, borderRadius: 8, padding: "10px 14px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>{label}</div>
            <div style={{ fontSize: 15, fontWeight: 800, color: col, fontFamily: "monospace" }}>{val}</div>
          </div>
        ))}
      </div>
      <div style={{ background: C.panel, borderRadius: 10, padding: 16, border: `1px solid ${C.border}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>DAILY LOSS METER</span>
          <span style={{ fontSize: 10, color: ddCol, fontWeight: 700 }}>{ddPct.toFixed(1)}% of ₹{maxDailyLoss} limit</span>
        </div>
        <div style={{ height: 8, background: C.border, borderRadius: 4 }}>
          <div style={{ height: "100%", width: `${ddPct}%`, background: ddCol, borderRadius: 4, transition: "width 0.5s" }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
          <span style={{ fontSize: 9, color: C.muted }}>₹0</span>
          <span style={{ fontSize: 9, color: C.orange }}>₹2,500 (50%)</span>
          <span style={{ fontSize: 9, color: C.red }}>₹5,000 (100%)</span>
        </div>
      </div>
    </div>
  )
}

const REGIME_META = {
  trending_bull:  { label: "▲ TREND BULL",    colour: "#00e87a" },
  trending_bear:  { label: "▼ TREND BEAR",    colour: "#ff3d5a" },
  range:          { label: "↔ RANGE",          colour: "#3b82f6" },
  reversal_watch: { label: "⚡ REVERSAL",      colour: "#f59e0b" },
  opening:        { label: "⏳ OPENING RANGE", colour: "#8b5cf6" },
  closed:         { label: "○ CLOSED",         colour: "#3a5070" },
}

function PcrGauge({ pcr = 1.0 }) {
  const pct = Math.min(Math.max((pcr - 0.5) / 1.0, 0), 1)
  const angle = pct * 180 - 90
  const rad = (angle * Math.PI) / 180
  const r = 28, cx = 36, cy = 36
  const nx = cx + r * Math.sin(rad), ny = cy - r * Math.cos(rad)
  const col = pcr > 1.3 ? "#00e87a" : pcr < 0.7 ? "#ff3d5a" : "#3b82f6"
  const arcPath = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`
  return (
    <svg width={72} height={44} viewBox="0 0 72 44">
      <path d={arcPath} fill="none" stroke="#1a2840" strokeWidth={5} strokeLinecap="round" />
      <path d={arcPath} fill="none" stroke={col} strokeWidth={5} strokeLinecap="round" strokeDasharray={`${pct * 88} 88`} opacity={0.8} />
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={col} strokeWidth={2} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={3} fill={col} />
      <text x={cx} y={cy + 14} textAnchor="middle" fill={col} fontSize={10} fontWeight="800" fontFamily="monospace">{pcr?.toFixed(2)}</text>
    </svg>
  )
}

function OiBar({ ceOi = 0, peOi = 0 }) {
  const total = (ceOi + peOi) || 1
  const cePct = (ceOi / total) * 100
  const pePct = (peOi / total) * 100
  const fmtL  = n => n > 1e6 ? `${(n/1e6).toFixed(1)}M` : n > 1e3 ? `${(n/1e3).toFixed(0)}K` : String(n)
  return (
    <div style={{ width: 140 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 8, color: "#3a5070", marginBottom: 3 }}>
        <span style={{ color: "#ff3d5a" }}>CE {fmtL(ceOi)}</span>
        <span style={{ color: "#00e87a" }}>PE {fmtL(peOi)}</span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: "#1a2840", overflow: "hidden", display: "flex" }}>
        <div style={{ width: `${cePct}%`, background: "#ff3d5a", opacity: 0.8 }} />
        <div style={{ width: `${pePct}%`, background: "#00e87a", opacity: 0.8 }} />
      </div>
    </div>
  )
}

function ContextBar({ marketCtx, astro }) {
  const ctx    = marketCtx || {}
  const regime = REGIME_META[ctx.regime] || { label: ctx.regime || "—", colour: "#3a5070" }
  const cell = (label, children) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 8, color: "#3a5070", letterSpacing: 1, fontWeight: 700 }}>{label}</span>
      {children}
    </div>
  )
  return (
    <div style={{ marginBottom: 10, background: "#0a1220", borderRadius: 8, padding: "10px 16px", border: "1px solid #1a2840", display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap", overflowX: "auto" }}>
      {cell("REGIME", <span style={{ fontSize: 11, fontWeight: 800, color: regime.colour, background: regime.colour + "18", borderRadius: 4, padding: "2px 8px", border: `1px solid ${regime.colour}30`, letterSpacing: 0.5, whiteSpace: "nowrap" }}>{regime.label}</span>)}
      {cell("PCR", <PcrGauge pcr={ctx.pcr} />)}
      {cell("OPEN INTEREST", <OiBar ceOi={ctx.total_ce_oi} peOi={ctx.total_pe_oi} />)}
      {cell("OI DELTA",
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 9, color: (ctx.ce_oi_delta || 0) > 0 ? "#ff3d5a" : "#00e87a", fontWeight: 700 }}>CE {(ctx.ce_oi_delta || 0) > 0 ? "+" : ""}{ctx.ce_oi_delta?.toLocaleString() || "—"}</span>
          <span style={{ fontSize: 9, color: (ctx.pe_oi_delta || 0) > 0 ? "#00e87a" : "#ff3d5a", fontWeight: 700 }}>PE {(ctx.pe_oi_delta || 0) > 0 ? "+" : ""}{ctx.pe_oi_delta?.toLocaleString() || "—"}</span>
        </div>
      )}
      <div style={{ width: 1, height: 40, background: "#1a2840", flexShrink: 0 }} />
      {cell("OPENING RANGE",
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {ctx.or_locked ? (
            <><span style={{ fontSize: 9, color: "#00e87a", fontWeight: 700 }}>H: {ctx.or_high?.toFixed(0) || "—"}</span><span style={{ fontSize: 9, color: "#ff3d5a", fontWeight: 700 }}>L: {ctx.or_low?.toFixed(0) || "—"}</span></>
          ) : <span style={{ fontSize: 9, color: "#f59e0b", fontWeight: 700 }}>NOT LOCKED</span>}
        </div>
      )}
      {cell("PREV DAY H/L",
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 9, fontWeight: 700, color: ctx.prev_day_breakout_bull ? "#00e87a" : "#3a5070" }}>H: {ctx.prev_day_high?.toFixed(0) || "—"}{ctx.prev_day_breakout_bull ? " ▲" : ""}</span>
          <span style={{ fontSize: 9, fontWeight: 700, color: ctx.prev_day_breakout_bear ? "#ff3d5a" : "#3a5070" }}>L: {ctx.prev_day_low?.toFixed(0) || "—"}{ctx.prev_day_breakout_bear ? " ▼" : ""}</span>
        </div>
      )}
      {cell("CONFIDENCE",
        <span style={{ fontSize: 11, fontWeight: 800, color: ctx.confidence_label === "HIGH" ? "#00e87a" : ctx.confidence_label === "MEDIUM" ? "#f59e0b" : "#ff3d5a" }}>{ctx.confidence_label || "—"} {ctx.confidence != null ? `${ctx.confidence.toFixed(0)}%` : ""}</span>
      )}
      {cell("ATM / MAX PAIN",
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 9, color: "#3b82f6", fontWeight: 700 }}>ATM: {ctx.atm_strike || "—"}</span>
          <span style={{ fontSize: 9, color: "#8b5cf6", fontWeight: 700 }}>MP: {ctx.max_pain || "—"}</span>
        </div>
      )}
      <div style={{ marginLeft: "auto", fontSize: 8, color: "#3a5070", textAlign: "right", whiteSpace: "nowrap" }}>OI: {ctx.oi_updated_at || "—"}</div>
    </div>
  )
}


// ── Strategy Lab Component ────────────────────────────────────────────────────
function StrategyLab({ nifty, vix }) {
  const [recs, setRecs] = useState(null)
  const [loading, setLoading] = useState(true)
  const [deploying, setDeploying] = useState(null)
  const [modal, setModal] = useState(null)
  const [toast, setToast] = useState("")

  useEffect(() => {
    async function fetchRecs() {
      setLoading(true)
      try {
        const r = await axios.get(`${API}/api/strategy-recommendations`)
        setRecs(r.data)
      } catch {
        setRecs(null)
      }
      setLoading(false)
    }
    fetchRecs()
    const id = setInterval(fetchRecs, 30000)
    return () => clearInterval(id)
  }, [])

  function fmtRs(n) {
    if (n === -1) return "Unlimited"
    if (n >= 100000) return "₹" + (n/100000).toFixed(1) + "L"
    return "₹" + Number(n).toLocaleString("en-IN")
  }

  async function deployStrategy(s) {
    setModal(null)
    setDeploying(s.id)
    try {
      const r = await axios.post(`${API}/api/strategy/deploy`, { id: s.id, legs: s.legs })
      if (r.data.success) {
        setToast(r.data.paper ? `PAPER: ${s.name} simulated` : `✅ ${s.name} deployed — ${s.legs.length} orders placed`)
      } else {
        setToast(`❌ Deploy failed: ${r.data.error}`)
      }
    } catch (e) {
      setToast(`❌ Error: ${e.message}`)
    }
    setDeploying(null)
    setTimeout(() => setToast(""), 5000)
  }

  function exportPDF(recs, strategies) {
    const lines = []
    lines.push("STRATEGY LAB REPORT")
    lines.push("=".repeat(50))
    lines.push(`Generated: ${new Date().toLocaleString("en-IN")}`)
    lines.push(`Nifty: ${recs.nifty?.toFixed(2)} | VIX: ${recs.vix?.toFixed(2)} | PCR: ${recs.pcr?.toFixed(2)} | ATM: ${recs.atm} | Regime: ${(recs.regime||"").toUpperCase()}`)
    lines.push("")
    lines.push("TOP PICK: " + (strategies[0]?.name || "None"))
    lines.push("Regime fit: " + (strategies[0]?.score || 0) + "/100")
    lines.push("")
    lines.push("ALL STRATEGIES")
    lines.push("-".repeat(50))
    strategies.forEach(s => {
      lines.push(`${s.name} [${s.badge}] — Score: ${s.score}/100`)
      lines.push(`  Type: ${s.type}`)
      lines.push(`  When: ${s.when}`)
      lines.push(`  Margin: ${s.margin} | Max Loss: ${s.max_risk === -1 ? "Unlimited" : s.max_risk} | Max Profit: ${s.max_profit === -1 ? "Unlimited" : s.max_profit}`)
      lines.push(`  Legs: ${s.legs?.map(l => `${l.action} ${l.strike}${l.type}`).join(", ")}`)
      lines.push(`  Conditions: ${s.conditions?.map(c => `${c.met ? "✓" : "✗"} ${c.label}`).join(" | ")}`)
      lines.push("")
    })
    const blob = new Blob([lines.join("\n")], { type: "text/plain" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `strategy-report-${new Date().toISOString().slice(0,10)}.txt`
    a.click()
    URL.revokeObjectURL(url)
    setToast("📄 Strategy report exported!")
    setTimeout(() => setToast(""), 3000)
  }
  if (loading) return (
    <div style={{ color: C.muted, textAlign: "center", padding: "40px 0", fontSize: 12 }}>
      Loading strategy recommendations...
    </div>
  )

  if (!recs) return (
    <div style={{ color: C.muted, textAlign: "center", padding: "40px 0", fontSize: 12 }}>
      Strategy API unavailable — add routes to dashboard/api.py
    </div>
  )

const strategies = recs.strategies ||[]
const topPick = strategies.length > 0 ? strategies[0] : null
const recommended = strategies.filter(s => s.recommended)

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Market snapshot */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px,1fr))", gap: 8 }}>
        {[
          { label: "NIFTY SPOT", val: recs.nifty?.toFixed(2) || "—", col: C.text },
          { label: "INDIA VIX",  val: recs.vix?.toFixed(2) || "—",   col: (recs.vix||0) > 18 ? C.red : C.green },
          { label: "PCR (OI)",   val: recs.pcr?.toFixed(2) || "—",   col: C.text },
          { label: "ATM STRIKE", val: recs.atm?.toFixed(0) || "—",   col: C.cyan },
          { label: "REGIME",     val: (recs.regime||"—").toUpperCase(), col: C.blue },
          { label: "OR WIDTH",   val: recs.or_width ? `${recs.or_width?.toFixed(0)} pts` : "—", col: C.muted },
        ].map(({ label, val, col }) => (
          <div key={label} style={{ background: C.panel, borderRadius: 8, padding: "10px 12px", border: `1px solid ${C.border}` }}>
            <div style={{ fontSize: 9, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{label}</div>
            <div style={{ fontSize: 15, fontWeight: 800, color: col, fontFamily: "monospace", marginTop: 3 }}>{val}</div>
          </div>
        ))}
      </div>

      {/* ── Top Recommendation Banner ── */}
      {topPick && (
        <div style={{
          background: topPick.recommended ? C.green+"15" : C.orange+"15",
          border: `1px solid ${topPick.recommended ? C.green : C.orange}40`,
          borderRadius: 10, padding: "14px 18px",
          display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10
        }}>
          <div>
            <div style={{ fontSize: 10, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 4 }}>
              🎯 TODAY'S TOP PICK
            </div>
            <div style={{ fontSize: 18, fontWeight: 900, color: topPick.recommended ? C.green : C.orange, fontFamily: "monospace" }}>
              {topPick.name}
            </div>
            <div style={{ fontSize: 11, color: C.muted, marginTop: 4 }}>
              Regime fit score: <b style={{ color: C.text }}>{topPick.score}/100</b> &nbsp;·&nbsp;
              {recommended.length > 0
                ? `${recommended.length} strategy${recommended.length > 1 ? "ies" : ""} recommended today`
                : "No strategy fully recommended — trade cautiously"}
            </div>
            <div style={{ fontSize: 11, color: C.muted, marginTop: 2 }}>{topPick.when}</div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              onClick={() => topPick.recommended && setModal(topPick)}
              style={{
                padding: "8px 16px", borderRadius: 6, fontSize: 11, fontWeight: 700,
                fontFamily: "monospace", cursor: topPick.recommended ? "pointer" : "not-allowed",
                border: `1px solid ${topPick.recommended ? C.green : C.border}40`,
                background: topPick.recommended ? C.green+"25" : C.panel,
                color: topPick.recommended ? C.green : C.muted,
              }}
            >
              {topPick.recommended ? "⚡ DEPLOY TOP PICK" : "MONITOR ONLY"}
            </button>
            <button
              onClick={() => exportPDF(recs, strategies)}
              style={{
                padding: "8px 16px", borderRadius: 6, fontSize: 11, fontWeight: 700,
                fontFamily: "monospace", cursor: "pointer",
                border: `1px solid ${C.cyan}40`,
                background: C.cyan+"15", color: C.cyan,
              }}
            >
              📄 EXPORT PDF
            </button>
          </div>
        </div>
      )}

      {/* Strategy cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(290px,1fr))", gap: 10 }}>
        {strategies.map(s => {
          const scoreCol = s.score >= 70 ? C.green : s.score >= 50 ? C.orange : C.red
          const isBlocked = s.score < 35
          return (
            <div key={s.id} style={{
              background: C.card, borderRadius: 12, padding: 16,
              border: s.recommended ? `2px solid ${C.green}` : `1px solid ${C.border}`,
              display: "flex", flexDirection: "column", gap: 10,
              opacity: isBlocked ? 0.6 : 1,
            }}>
              {/* Header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 800, color: C.text }}>{s.name}</div>
                  <div style={{ fontSize: 10, color: C.muted, marginTop: 2 }}>{s.type}</div>
                </div>
                <span style={{
                  fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 99,
                  background: s.badge_cls === "badge-green" ? C.green+"18" : s.badge_cls === "badge-blue" ? C.blue+"18" : C.orange+"18",
                  color: s.badge_cls === "badge-green" ? C.green : s.badge_cls === "badge-blue" ? C.blue : C.orange,
                  border: `1px solid ${s.badge_cls === "badge-green" ? C.green : s.badge_cls === "badge-blue" ? C.blue : C.orange}40`,
                }}>{s.badge}</span>
              </div>

              {/* Score bar */}
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: C.muted, marginBottom: 4 }}>
                  <span>Regime fit</span>
                  <span style={{ color: scoreCol, fontWeight: 700 }}>{s.score}/100</span>
                </div>
                <div style={{ height: 4, background: C.border, borderRadius: 2 }}>
                  <div style={{ height: "100%", width: `${s.score}%`, background: scoreCol, borderRadius: 2 }} />
                </div>
              </div>

              {/* Conditions */}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {s.conditions?.map((c, i) => (
                  <span key={i} style={{
                    fontSize: 10, padding: "2px 8px", borderRadius: 4,
                    background: c.met ? C.green+"15" : C.red+"15",
                    border: `0.5px solid ${c.met ? C.green : C.red}40`,
                    color: c.met ? C.green : C.red,
                  }}>{c.met ? "✓" : "✗"} {c.label}</span>
                ))}
              </div>

              {/* Risk metrics */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
                {[
                  { label: "MARGIN", val: fmtRs(s.margin), col: C.text },
                  { label: "MAX LOSS", val: fmtRs(s.max_risk), col: C.red },
                  { label: "MAX PROFIT", val: fmtRs(s.max_profit), col: C.green },
                ].map(({ label, val, col }) => (
                  <div key={label} style={{ background: C.panel, borderRadius: 6, padding: "7px 8px", border: `1px solid ${C.border}` }}>
                    <div style={{ fontSize: 8, color: C.muted, fontWeight: 700 }}>{label}</div>
                    <div style={{ fontSize: 12, fontWeight: 700, color: col, marginTop: 2, fontFamily: "monospace" }}>{val}</div>
                  </div>
                ))}
              </div>

              {/* Legs preview */}
              <div style={{ background: C.panel, borderRadius: 6, padding: "8px 10px", border: `1px solid ${C.border}` }}>
                {s.legs?.map((leg, i) => (
                  <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 11, padding: "2px 0", color: C.muted }}>
                    <span style={{ color: leg.action === "SELL" ? C.red : C.green, fontWeight: 700 }}>{leg.action}</span>
                    <span>{leg.strike} {leg.type}</span>
                    <span>Qty {leg.qty}</span>
                  </div>
                ))}
              </div>

              {/* When to use */}
              <div style={{ fontSize: 11, color: C.muted, borderLeft: `2px solid ${C.dim}`, paddingLeft: 8, lineHeight: 1.5 }}>
                {s.when}
              </div>

              {/* Deploy button */}
              <button
                onClick={() => !isBlocked && setModal(s)}
                disabled={isBlocked || deploying === s.id}
                style={{
                  width: "100%", padding: "8px 0", borderRadius: 6, fontSize: 11,
                  fontWeight: 700, cursor: isBlocked ? "not-allowed" : "pointer",
                  fontFamily: "monospace", letterSpacing: ".05em",
                  border: s.recommended ? `1px solid ${C.green}40` : `1px solid ${C.border}`,
                  background: s.recommended ? C.green+"18" : C.panel,
                  color: s.recommended ? C.green : isBlocked ? C.muted : C.orange,
                }}
              >
                {deploying === s.id ? "DEPLOYING..." : isBlocked ? "NOT RECOMMENDED" : s.recommended ? "DEPLOY STRATEGY" : "DEPLOY WITH CAUTION"}
              </button>
            </div>
          )
        })}
      </div>

      {/* Confirmation modal */}
      {modal && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 999, display: "flex", alignItems: "center", justifyContent: "center" }}
          onClick={() => setModal(null)}>
          <div style={{ background: C.card, borderRadius: 12, border: `1px solid ${C.border}`, padding: 20, maxWidth: 400, width: "90%", maxHeight: "80vh", overflowY: "auto" }}
            onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 14, fontWeight: 800, color: C.text, marginBottom: 14 }}>Deploy: {modal.name}</div>
            {modal.legs?.map((l, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between", padding: "7px 0", borderBottom: `1px solid ${C.border}`, fontSize: 12 }}>
                <span style={{ color: C.muted }}>{l.action} {l.strike} {l.type}</span>
                <span style={{ color: C.text, fontWeight: 700 }}>Qty {l.qty}</span>
              </div>
            ))}
            {[
              ["Margin required", fmtRs(modal.margin), C.text],
              ["Max loss", fmtRs(modal.max_risk), C.red],
              ["Max profit", fmtRs(modal.max_profit), C.green],
              ["Risk/Reward", modal.rr, C.cyan],
            ].map(([k, v, col]) => (
              <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "7px 0", borderBottom: `1px solid ${C.border}`, fontSize: 12 }}>
                <span style={{ color: C.muted }}>{k}</span>
                <span style={{ color: col, fontWeight: 700 }}>{v}</span>
              </div>
            ))}
            <div style={{ marginTop: 10, padding: "8px 10px", background: C.orange+"18", borderRadius: 6, fontSize: 11, color: C.orange }}>
              ⚠ This places real orders via Upstox. Confirm only if conditions match.
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 14 }}>
              <button onClick={() => deployStrategy(modal)} style={{ flex: 1, padding: 9, borderRadius: 6, border: `1px solid ${C.green}40`, background: C.green+"18", color: C.green, fontSize: 12, fontWeight: 700, cursor: "pointer", fontFamily: "monospace" }}>
                CONFIRM DEPLOY
              </button>
              <button onClick={() => setModal(null)} style={{ flex: 1, padding: 9, borderRadius: 6, border: `1px solid ${C.border}`, background: C.panel, color: C.muted, fontSize: 12, fontWeight: 700, cursor: "pointer", fontFamily: "monospace" }}>
                CANCEL
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      {toast && (
        <div style={{ position: "fixed", bottom: 20, right: 20, background: C.card, border: `1px solid ${C.border}`, borderRadius: 8, padding: "10px 16px", fontSize: 12, color: C.text, zIndex: 1000 }}>
          {toast}
        </div>
      )}
    </div>
  )
}


// ── Bot Health Monitor ────────────────────────────────────────────────────────
function BotHealthMonitor({ wsStatus, data, token }) {
  const [lastTickAge, setLastTickAge] = useState(null)
  const [tickRate, setTickRate] = useState(0)
  const lastTickRef = useRef(null)
  const tickCountRef = useRef(0)
  const tickWindowRef = useRef([])

  // Track tick rate from nifty_price changes
  const niftyPrice = data?.market?.nifty_price || 0
  useEffect(() => {
    if (niftyPrice > 0) {
      const now = Date.now()
      lastTickRef.current = now
      tickWindowRef.current.push(now)
      // Keep only last 10 seconds of ticks
      tickWindowRef.current = tickWindowRef.current.filter(t => now - t < 10000)
      tickCountRef.current = tickWindowRef.current.length
    }
  }, [niftyPrice])

  useEffect(() => {
    const id = setInterval(() => {
      if (lastTickRef.current) {
        setLastTickAge(Math.floor((Date.now() - lastTickRef.current) / 1000))
      }
      setTickRate(Math.round(tickWindowRef.current.filter(t => Date.now() - t < 10000).length / 10))
    }, 1000)
    return () => clearInterval(id)
  }, [])

  const tickOk    = lastTickAge !== null && lastTickAge < 10
  const tickWarn  = lastTickAge !== null && lastTickAge >= 10 && lastTickAge < 30
  const tickDead  = lastTickAge === null || lastTickAge >= 30
  const wsOk      = wsStatus === "CONNECTED"
  const brokerOn  = Object.values(data?.global?.broker_status || {}).some(v => v === "CONNECTED")
  const allOk     = tickOk && wsOk && brokerOn

  const dot = (ok, warn) => ({
    width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
    background: ok ? C.green : warn ? C.orange : C.red,
    boxShadow: ok ? `0 0 4px ${C.green}` : warn ? `0 0 4px ${C.orange}` : `0 0 4px ${C.red}`,
  })

  const checks = [
    { label: "WEBSOCKET",   ok: wsOk,     warn: false,      val: wsStatus === "CONNECTED" ? "LIVE" : wsStatus },
    { label: "BROKER",      ok: brokerOn, warn: false,      val: brokerOn ? "CONNECTED" : "OFFLINE" },
    { label: "NIFTY TICKS", ok: tickOk,   warn: tickWarn,   val: tickDead ? "NO DATA" : tickWarn ? `${lastTickAge}s ago` : `${tickRate}/s` },
    { label: "OPTION TICKS",ok: niftyPrice > 0, warn: false, val: data?.market?.option_price > 0 ? `₹${data.market.option_price?.toFixed(2)}` : "NO DATA" },
  ]

  return (
    <div style={{
      background: C.card, borderRadius: 10, padding: "12px 16px",
      border: `1px solid ${allOk ? C.green+"40" : C.orange+"40"}`,
      display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap",
      flex: 1,
    }}>
      {/* Overall status */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 120 }}>
        <div style={{
          width: 10, height: 10, borderRadius: "50%",
          background: allOk ? C.green : tickDead ? C.red : C.orange,
          boxShadow: `0 0 6px ${allOk ? C.green : tickDead ? C.red : C.orange}`,
          animation: allOk ? "none" : "pulse 1s infinite",
        }} />
        <div>
          <div style={{ fontSize: 12, fontWeight: 800, color: allOk ? C.green : tickDead ? C.red : C.orange }}>
            {allOk ? "BOT HEALTHY" : tickDead ? "NO MARKET DATA" : "DEGRADED"}
          </div>
          <div style={{ fontSize: 9, color: C.muted }}>
            {lastTickAge !== null ? `Last tick ${lastTickAge}s ago` : "Waiting for ticks..."}
          </div>
        </div>
      </div>

      {/* Individual checks */}
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {checks.map(c => (
          <div key={c.label} style={{
            background: C.panel, borderRadius: 6, padding: "5px 10px",
            border: `0.5px solid ${c.ok ? C.green+"30" : c.warn ? C.orange+"30" : C.red+"30"}`,
            display: "flex", flexDirection: "column", gap: 1,
          }}>
            <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>{c.label}</div>
            <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <div style={{ width: 5, height: 5, borderRadius: "50%", background: c.ok ? C.green : c.warn ? C.orange : C.red }} />
              <span style={{ fontSize: 11, fontWeight: 700, color: c.ok ? C.green : c.warn ? C.orange : C.red, fontFamily: "monospace" }}>
                {c.val}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

const DEFAULT = {
  global: { total_pnl: 0, active_strategies: 0, total_strategies: 0, system_health: "OK", broker_status: {}, paper_trade: false },
  strategies: {}, vix: null,
  market: { nifty_price: 0, option_price: 0 },
}

// ── Bot Status Bar (#25) ─────────────────────────────────────────────────────
function BotStatusBar() {
  const [status, setStatus] = React.useState(null)
  React.useEffect(() => {
    async function fetch() {
      try {
        const r = await axios.get(`${API}/api/bot-status`)
        setStatus(r.data)
      } catch {}
    }
    fetch()
    const id = setInterval(fetch, 5000)
    return () => clearInterval(id)
  }, [])
  if (!status) return null

  const colMap = { green: C.green, red: C.red, orange: C.orange }
  const col = colMap[status.status_colour] || C.muted
  const capPct = status.capital_pct || 0
  const capCol = capPct > 80 ? C.red : capPct > 50 ? C.orange : C.green
  const pnlPct = Math.abs(status.pnl_pct_of_limit || 0)
  const pnlCol = (status.daily_pnl || 0) >= 0 ? C.green : pnlPct > 80 ? C.red : pnlPct > 50 ? C.orange : C.red

  return (
    <div style={{ background: C.card, borderRadius: 10, padding: "10px 16px", border: `1px solid ${col}40`, marginBottom: 12, display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>
      {/* Trading Status */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 160 }}>
        <div style={{ width: 10, height: 10, borderRadius: "50%", background: col, boxShadow: `0 0 6px ${col}` }} />
        <div>
          <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>TRADING STATUS</div>
          <div style={{ fontSize: 13, fontWeight: 800, color: col }}>{status.trading_status}</div>
        </div>
      </div>
      {/* Halt/Block Reason */}
      {(status.halt_reason || status.block_reason) && (
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 2 }}>
            {status.is_halted ? "HALT REASON" : "BLOCK REASON"}
          </div>
          <div style={{ fontSize: 10, color: col, fontWeight: 600 }}>
            {status.halt_reason || status.block_reason}
          </div>
        </div>
      )}
      {/* Capital Bar */}
      <div style={{ minWidth: 160 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
          <span style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>CAPITAL DEPLOYED</span>
          <span style={{ fontSize: 8, color: capCol, fontWeight: 800 }}>{capPct}%</span>
        </div>
        <div style={{ height: 5, background: C.border, borderRadius: 3, marginBottom: 3 }}>
          <div style={{ height: "100%", width: `${Math.min(capPct, 100)}%`, background: capCol, borderRadius: 3, transition: "width 0.5s" }} />
        </div>
        <div style={{ fontSize: 9, color: C.muted }}>
          ₹{status.capital_deployed?.toLocaleString()} / ₹{status.capital_max?.toLocaleString()} 
          <span style={{ color: C.green, marginLeft: 6 }}>₹{status.capital_remaining?.toLocaleString()} free</span>
        </div>
      </div>
      {/* Daily P&L vs Limit */}
      <div style={{ minWidth: 160 }}>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
          <span style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1 }}>DAILY P&L</span>
          <span style={{ fontSize: 8, color: pnlCol, fontWeight: 800 }}>{pnlPct.toFixed(1)}%</span>
        </div>
        <div style={{ height: 5, background: C.border, borderRadius: 3, marginBottom: 3 }}>
          <div style={{ height: "100%", width: `${Math.min(pnlPct, 100)}%`, background: pnlCol, borderRadius: 3, transition: "width 0.5s" }} />
        </div>
        <div style={{ fontSize: 9, color: C.muted }}>
          P&L: <span style={{ color: pnlC(status.daily_pnl) }}>₹{status.daily_pnl?.toFixed(2)}</span>
          <span style={{ marginLeft: 6 }}>Limit: ₹{status.daily_loss_limit?.toFixed(0)}</span>
        </div>
      </div>
      {/* Trades Today */}
      <div style={{ textAlign: "center", minWidth: 80 }}>
        <div style={{ fontSize: 8, color: C.muted, fontWeight: 700, letterSpacing: 1, marginBottom: 2 }}>TRADES TODAY</div>
        <div style={{ fontSize: 22, fontWeight: 800, color: C.text, fontFamily: "monospace" }}>{status.trades_today}</div>
      </div>
    </div>
  )
}

export default function App() {
  const [data,     setData]     = useState(DEFAULT)
  const [trades,   setTrades]   = useState([])
  const [wsStatus, setWsStatus] = useState("CONNECTING")
  const [tab,      setTab]      = useState("positions")
  const [token,    setToken]    = useState(null)
  const capital = useCapital()
  // Per-strategy capital tracking from risk_manager (ground truth + drift
  // detection) -- single source of truth also used by the actual capital
  // guard that blocks trades, replacing each StratCard's own disconnected,
  // hardcoded local calc (see 31-Jul capital-tracking investigation)
  const [stratCapital, setStratCapital] = useState(null)
  useEffect(() => {
    async function fetchStratCapital() {
      try {
        const r = await axios.get(`${API}/api/capital`)
        if (r.data) setStratCapital(r.data)
      } catch {}
    }
    fetchStratCapital()
    const id = setInterval(fetchStratCapital, 5000)
    return () => clearInterval(id)
  }, [])
  const wsRef   = useRef(null)
  const [soundEnabled, setSoundEnabled] = useState(false)
  const prevTradeCount = useRef(0)
  const [now, setNow] = useState(new Date())

  useEffect(() => { const id = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(id) }, [])

  // Fetch token for expiry countdown
  useEffect(() => {
    async function fetchToken() {
      try {
        const r = await axios.get(`${API}/api/token-info`)
        if (r.data?.token) setToken(r.data.token)
      } catch {}
    }
    fetchToken()
    const id = setInterval(fetchToken, 60000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(WS)
      wsRef.current = ws
      ws.onopen    = () => setWsStatus("CONNECTED")
      ws.onmessage = (e) => { try { setData(JSON.parse(e.data)) } catch {} }
      ws.onclose   = () => { setWsStatus("RECONNECTING"); setTimeout(connect, 3000) }
      ws.onerror   = () => { ws.close() }
    }
    connect()
    return () => { if (wsRef.current) wsRef.current.close() }
  }, [])

  useEffect(() => {
    async function fetchTrades() {
      try {
        const res = await axios.get(`${API}/api/trades?limit=500`)
        setTrades(res.data.trades || [])
      } catch {}
    }
    fetchTrades()
    const id = setInterval(fetchTrades, 3000)
    return () => clearInterval(id)
  }, [])

  async function handleStop(name) {
    try { await axios.post(`${API}/api/strategy/${name}/stop`) }
    catch (e) { alert(`Stop failed: ${e.response?.data?.error || e.message}`) }
  }
  async function handleReset(name) {
    try { await axios.post(`${API}/api/strategy/${name}/reset`) }
    catch (e) { alert(`Reset failed: ${e.response?.data?.error || e.message}`) }
  }
  const [killActive, setKillActive] = React.useState(false)
  async function handleKillSwitch() {
    if (!window.confirm('🚨 KILL SWITCH\n\nThis will:\n1. HALT all new trading\n2. CLOSE all open positions\n3. Send Telegram alert\n\nAre you sure?')) return
    try {
      setKillActive(true)
      await axios.post(`${API}/api/killswitch?flatten=true`)
      alert('✅ Kill switch activated — all positions closed, trading halted')
    } catch(e) {
      alert(`Kill switch failed: ${e.response?.data?.error || e.message}`)
    } finally {
      setKillActive(false)
    }
  }

  const g = data.global, s = data.strategies, vix = data.vix, market = data.market || {}
  const openCount = trades.filter(t => t.status === "OPEN").length
  const todayStr  = now.toISOString().slice(0, 10)
  const todayPnl  = trades.filter(t => t.status === "CLOSED" && t.exit_time?.slice(0, 10) === todayStr).reduce((s, t) => s + (t.realised_pnl || 0), 0)
  const unrealisedTotal = trades.filter(t => t.status === "OPEN").reduce((s, t) => s + (t.unrealised_pnl || 0), 0)
  const currentPnl = todayPnl + unrealisedTotal
  const brokerOn  = Object.values(g.broker_status || {}).some(v => v === "CONNECTED")

  // Sound alerts
  useEffect(() => {
    if (!soundEnabled) return
    const closedToday = trades.filter(t => t.status === "CLOSED" && t.exit_time?.slice(0,10) === todayStr)
    const newCount = closedToday.length
    if (newCount > prevTradeCount.current && prevTradeCount.current > 0) {
      const last = closedToday[closedToday.length - 1]
      if ((last?.realised_pnl || 0) >= 0) playTradeWin(); else playTradeLoss()
    }
    if (todayPnl < -4000 && prevTradeCount.current > 0) playLossAlarm()
    prevTradeCount.current = newCount
  }, [trades.length, soundEnabled])

  const TABS = [
    { key: "positions", label: `POSITIONS (${openCount})` },
    { key: "ledger",    label: `LEDGER (${trades.length})` },
    { key: "execlog",   label: "EXEC LOG" },
    { key: "perf",      label: "PERFORMANCE" },
    { key: "risk",      label: "RISK & CAPITAL" },
    { key: "strategy",  label: "STRATEGY LAB" },
    { key: "banknifty", label: "📄 BANKNIFTY PAPER" },
  ]

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace", padding: "16px 20px" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;800&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: #060b14; }
        ::-webkit-scrollbar-thumb { background: #1a2840; border-radius: 4px; }
        button { font-family: inherit; }
      `}</style>

      {/* ── Header ── */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, paddingBottom: 14, borderBottom: `1px solid ${C.border}` }}>
        <div>
          <div style={{ fontSize: 17, fontWeight: 800, color: "#fff", letterSpacing: -0.5 }}>◈ ALGO TRADING SYSTEM</div>
          <div style={{ fontSize: 9, color: C.muted, marginTop: 3, letterSpacing: 2 }}>SAVIOUR COMBO · SURVIVOR ALGO · WAVE EXTRACTOR</div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 11, color: C.muted }}>{now.toLocaleTimeString("en-IN")}</span>
          {/* WS Status — now more prominent */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, background: C.card, borderRadius: 6, padding: "4px 10px", border: `1px solid ${wsStatus === "CONNECTED" ? C.green : wsStatus === "RECONNECTING" ? C.orange : C.red}40` }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: wsStatus === "CONNECTED" ? C.green : wsStatus === "RECONNECTING" ? C.orange : C.red, boxShadow: `0 0 6px ${wsStatus === "CONNECTED" ? C.green : C.red}` }} />
            <span style={{ fontSize: 10, fontWeight: 700, color: wsStatus === "CONNECTED" ? C.green : wsStatus === "RECONNECTING" ? C.orange : C.red }}>
              {wsStatus === "CONNECTED" ? "WS LIVE" : wsStatus === "RECONNECTING" ? "RECONNECTING" : "WS OFFLINE"}
            </span>
          </div>
          <Pill label={brokerOn ? "BROKER ON" : "BROKER OFF"} colour={brokerOn ? C.green : C.red} />
          <Pill label={`PAPER: ${g.paper_trade ? "ON" : "OFF"}`} colour={g.paper_trade ? C.orange : C.blue} />
          <SoundControl enabled={soundEnabled} onToggle={() => setSoundEnabled(p => !p)} />
          <button onClick={handleKillSwitch} disabled={killActive}
            style={{ background: killActive ? "#888" : "#ff3d5a", color: "#fff", border: "none", borderRadius: 6, padding: "5px 14px", fontSize: 11, fontWeight: 800, cursor: killActive ? "not-allowed" : "pointer", letterSpacing: 0.5 }}>
            {killActive ? "STOPPING..." : "🚨 KILL"}
          </button>
        </div>
      </div>

      {/* ── Top stats ── */}
      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>
        <NiftyBox market={market} />
        <StatTile label="CURRENT P&L"    value={fmtRs(currentPnl)}   colour={pnlC(currentPnl)}   bg={pnlBg(currentPnl)} />
        <StatTile label="TODAY REALISED" value={fmtRs(todayPnl)}     colour={pnlC(todayPnl)}     bg={pnlBg(todayPnl)} />
        <StatTile label="OPEN POSITIONS" value={openCount}           colour={openCount > 0 ? C.blue : C.muted} />
        <StatTile label="ACTIVE STRATS"  value={`${g.active_strategies || 0}/${g.total_strategies || 0}`} colour={C.text} />
        <StatTile label="SYSTEM HEALTH"  value={g.system_health || "OK"} colour={g.system_health === "OK" ? C.green : C.red} />
        <VixBox vix={vix} />
        {/* Token Expiry + Auto-stop */}
        <TokenCountdown token={token} />
        <AutoStopCountdown />
      </div>

      {/* ── Bot Status Bar ── */}
      <BotStatusBar />

      {/* ── Bot Health + Paper Toggle Row ── */}
      <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap", alignItems: "stretch" }}>
        <PaperLiveToggle isPaper={g.paper_trade} />
        <BotHealthMonitor wsStatus={wsStatus} data={data} />
      </div>

      {/* ── Context Bar ── */}
      <div style={{ marginBottom: 12 }}>
        <ContextBar marketCtx={data.market_ctx} astro={data.astro} />
      </div>

      {/* ── Astro Calendar + Session Plan (PROMINENT) ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 380px", gap: 12, marginBottom: 14 }}>
        <AstroCalendarPanel />
        <SessionPlanPanel />
      </div>

      {/* ── Capital bar ── */}
      <div style={{ marginBottom: 14 }}>
        <CapitalBar trades={trades} global={g} capital={capital} />
      </div>

      {/* ── Strategy cards ── */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12, marginBottom: 16 }}>
        {["saviour_combo", "survivor", "wave_extractor", "nifty_gex"].map(name => (
          <StratCard key={name} name={name} data={s[name]} onStop={handleStop} onReset={handleReset} trades={trades} stratCapital={stratCapital} />
        ))}
      </div>

      {/* ── Bottom tabs ── */}
      <div style={{ background: C.card, borderRadius: 12, border: `1px solid ${C.border}`, overflow: "hidden" }}>
        <div style={{ display: "flex", borderBottom: `1px solid ${C.border}`, background: C.panel, overflowX: "auto" }}>
          {TABS.map(({ key, label }) => (
            <button key={key} onClick={() => setTab(key)} style={{ padding: "11px 18px", border: "none", background: "transparent", color: tab === key ? C.green : C.muted, fontWeight: 700, fontSize: 10, letterSpacing: 1, cursor: "pointer", borderBottom: tab === key ? `2px solid ${C.green}` : "2px solid transparent", whiteSpace: "nowrap" }}>{label}</button>
          ))}
        </div>
        <div style={{ padding: 18 }}>
          {tab === "positions" && <OpenPositions trades={trades} />}
          {tab === "ledger"    && <TradeLedger trades={trades} />}
          {tab === "execlog"   && <ExecutionLog trades={trades} />}
          {tab === "banknifty"  && <BankNiftyPanel />}
          {tab === "perf"      && <PerformancePanel trades={trades} />}
          {tab === "risk"      && <div style={{ display: "flex", flexDirection: "column", gap: 16 }}><AICapitalAdvisor /><OpportunityMeter /><CapitalIntelligencePanel trades={trades} /><RiskPanel trades={trades} global={g} /></div>}
          {tab === "strategy"  && <StrategyLab nifty={market?.nifty_price || 0} vix={vix?.value || 0} />}
        </div>
      </div>

      <div style={{ textAlign: "center", marginTop: 12, fontSize: 9, color: C.dim, letterSpacing: 1 }}>
        LAST UPDATE: {data.timestamp ? new Date(data.timestamp).toLocaleTimeString() : "—"} · {trades.length} TRADES LOADED
      </div>
    </div>
  )
}
