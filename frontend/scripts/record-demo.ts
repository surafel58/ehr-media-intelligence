// Record the README demo video: drives the running app like a person would, then converts it to MP4.
//
//   PW_BROWSER_PATH=<chromium browser>  FFMPEG_PATH=<ffmpeg with libx264>  node scripts/record-demo.ts
//
// Needs the app running on http://127.0.0.1:8000 (uv run python -m ehr_platform.demo).

import { execFileSync } from "node:child_process"
import path from "node:path"

import { type Locator, type Page, chromium } from "@playwright/test"

const BASE = process.env.DEMO_URL ?? "http://127.0.0.1:8000"
const FFMPEG = process.env.FFMPEG_PATH ?? "ffmpeg"
const OUT = path.resolve(import.meta.dirname, "../../docs/demo.mp4")
const RAW_DIR = path.resolve(import.meta.dirname, "../test-results/demo-video")
const size = { width: 1280, height: 800 }

// Playwright videos do not show the mouse, so draw one.
function showPointer() {
  window.addEventListener("DOMContentLoaded", () => {
    const dot = document.createElement("div")
    dot.style.cssText =
      "position:fixed;z-index:2147483647;left:-40px;top:-40px;width:20px;height:20px;margin:-10px 0 0 -10px;" +
      "border-radius:50%;background:rgba(11,110,130,.25);border:2px solid #0b6e82;pointer-events:none;" +
      "transition:transform .12s ease-out"
    document.body.appendChild(dot)
    document.addEventListener("mousemove", (e) => {
      dot.style.left = `${e.clientX}px`
      dot.style.top = `${e.clientY}px`
    }, true)
    document.addEventListener("mousedown", () => (dot.style.transform = "scale(.65)"), true)
    document.addEventListener("mouseup", () => (dot.style.transform = "scale(1)"), true)
  })
}

const pause = (page: Page, ms: number) => page.waitForTimeout(ms)

async function moveTo(page: Page, target: Locator) {
  await target.scrollIntoViewIfNeeded()
  const box = (await target.boundingBox())!
  await page.mouse.move(box.x + Math.min(box.width / 2, 120), box.y + box.height / 2, { steps: 28 })
}

async function click(page: Page, target: Locator) {
  await moveTo(page, target)
  await pause(page, 250)
  await page.mouse.down()
  await page.mouse.up()
}

async function typeQuery(page: Page, text: string) {
  const box = page.getByRole("searchbox", { name: "Search patient records" })
  await click(page, box)
  await box.fill("")
  await box.pressSequentially(text, { delay: 65 })
  try {
    await page.getByText(`for "${text}"`).waitFor({ timeout: 15_000 })
  } catch (error) {
    await page.screenshot({ path: path.join(RAW_DIR, "failed.png") })
    console.error(`search box holds "${await box.inputValue()}"`)
    throw error
  }
  await page.locator("section[aria-label='Results'][aria-busy='false']").waitFor()
}

async function scrollDrawer(page: Page, pixels: number) {
  const drawer = page.getByRole("dialog")
  const box = (await drawer.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 12 })
  for (let i = 0; i < 10; i++) {
    await page.mouse.wheel(0, pixels / 10)
    await pause(page, 60)
  }
}

const browser = await chromium.launch({ executablePath: process.env.PW_BROWSER_PATH })
const context = await browser.newContext({ viewport: size, recordVideo: { dir: RAW_DIR, size }, colorScheme: "light" })
await context.addInitScript(showPointer)
const page = await context.newPage()
await page.goto(BASE)
await page.evaluate(() => localStorage.clear())
await page.reload()
await page.getByRole("heading", { name: "Find any record in plain language" }).waitFor()
await pause(page, 1600)

let step = "start"
try {
  // 1. Search in plain language
  step = "1. Search in plain language"
  await typeQuery(page, "abnormal blood glucose")
  await pause(page, 2600)

  // 2. Open the lab result: patient banner, AI summary, flags and lab values
  step = "2. Open the lab result: patient banner, AI summary, flags and lab values"
  await click(page, page.getByRole("list", { name: "Search results" }).getByRole("article").first().getByRole("button"))
  await page.getByRole("heading", { name: "AI summary" }).waitFor()
  await page.evaluate(() => Promise.all(document.getAnimations().filter((a) => a.effect?.getComputedTiming().iterations !== Infinity).map((a) => a.finished.catch(() => null))))
  await page.getByRole("dialog").evaluate((el) => el.scrollTo({ top: 0 }))
  await pause(page, 2600)
  await scrollDrawer(page, 420)
  await pause(page, 2400)
  await scrollDrawer(page, 480)
  await pause(page, 2600)
  await page.keyboard.press("Escape")
  await pause(page, 900)

  // 3. Filter by record type
  step = "3. Filter by record type"
  await click(page, page.getByRole("combobox", { name: "Record type" }))
  await pause(page, 700)
  await click(page, page.getByRole("option", { name: "Lab and imaging reports" }))
  await pause(page, 600)
  await typeQuery(page, "bone scan for prostate cancer")
  await pause(page, 2800)
  await click(page, page.getByRole("button", { name: "Clear filters" }))
  await pause(page, 800)

  // 4. Another patient: home health records and the AI summary with its sources
  step = "4. Another patient: home health records and the AI summary with its sources"
  await typeQuery(page, "heart failure with a leg wound")
  await pause(page, 2200)
  const summary = page.getByRole("list", { name: "Search results" }).getByRole("article").filter({ hasText: "AI summary" }).first()
  await click(page, summary.getByRole("button"))
  await page.getByRole("heading", { name: "AI summary" }).waitFor()
  await page.evaluate(() => Promise.all(document.getAnimations().filter((a) => a.effect?.getComputedTiming().iterations !== Infinity).map((a) => a.finished.catch(() => null))))
  await pause(page, 2400)
  await scrollDrawer(page, 380)
  await pause(page, 2600)
  await page.keyboard.press("Escape")
  await pause(page, 900)

  // 5. Dark theme
  step = "5. Dark theme"
  await click(page, page.getByRole("button", { name: "Switch to dark theme" }))
  await pause(page, 2600)
  await page.mouse.wheel(0, 300)
  await pause(page, 2000)
} catch (error) {
  await page.screenshot({ path: path.join(RAW_DIR, "failed.png") })
  const firstLines = String((error as Error).message).split("\n").slice(0, 3).join("\n")
  console.error(`Failed during: ${step}\n${firstLines}`)
  throw error
}

await context.close()
const raw = await page.video()!.path()
await browser.close()

// Drop the blank first moment and encode H.264 so the file plays everywhere.
execFileSync(FFMPEG, ["-y", "-loglevel", "error", "-ss", "0.6", "-i", raw, "-c:v", "libx264", "-preset", "slow",
  "-crf", "26", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", OUT])
console.log(`Demo video written to ${OUT}`)
