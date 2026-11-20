import path from "node:path"

import AxeBuilder from "@axe-core/playwright"
import { test as base, expect, type Page } from "@playwright/test"

const SHOTS = path.join(import.meta.dirname, "screenshots")

// Every test fails if the browser logs an error.
const test = base.extend<{ consoleErrors: string[] }>({
  consoleErrors: [
    async ({ page }, use) => {
      const errors: string[] = []
      page.on("console", (m) => (m.type() === "error" ? errors.push(m.text()) : undefined))
      page.on("pageerror", (e) => errors.push(e.message))
      await use(errors)
      expect(errors, "browser console errors").toEqual([])
    },
    { auto: true },
  ],
})

const searchBox = (page: Page) => page.getByRole("searchbox", { name: "Search patient records" })
const results = (page: Page) => page.getByRole("list", { name: "Search results" }).getByRole("article")

async function search(page: Page, query: string) {
  await searchBox(page).fill(query)
  await expect(page.getByText(`for "${query}"`)).toBeVisible()
  await expect(page.getByRole("region", { name: "Results" })).toHaveAttribute("aria-busy", "false")
}

async function scan(page: Page, label: string) {
  // Measure the settled page: a fade-in in progress would make every color look too faint.
  await page.evaluate(() => Promise.all(document.getAnimations().filter((a) => a.effect?.getComputedTiming().iterations !== Infinity).map((a) => a.finished.catch(() => null))))
  const { violations } = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze()
  const serious = violations.filter((v) => v.impact === "serious" || v.impact === "critical")
  expect(serious.map((v) => `${label}: ${v.id} (${v.nodes.length}) ${v.help}`)).toEqual([])
}

test.beforeEach(async ({ page }) => {
  await page.goto("/")
  await page.evaluate(() => localStorage.clear())
  await page.reload()
})

test("start screen invites a search", async ({ page }) => {
  await expect(page.getByRole("heading", { level: 1, name: "Search patient records" })).toBeVisible()
  await expect(searchBox(page)).toBeFocused()
  await expect(page.getByRole("heading", { name: "Find any record in plain language" })).toBeVisible()
  await expect(page.getByRole("button", { name: "abnormal blood glucose" })).toBeVisible()
  await expect(page.getByText(/text passages indexed/)).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/start-light.png` })
})

test("typing shows up to five ranked results with every field", async ({ page }) => {
  await search(page, "abnormal blood glucose")
  const rows = results(page)
  const count = await rows.count()
  expect(count).toBeGreaterThan(0)
  expect(count).toBeLessThanOrEqual(5)

  const scores: number[] = []
  for (const row of await rows.all()) {
    await expect(row.getByText(/^\d+%/)).toBeVisible() // relevance score
    await expect(row.getByText(/Clinical note|Lab report|Imaging report|AI summary/).first()).toBeVisible() // type badge
    await expect(row.getByText(/DocumentReference|DiagnosticReport|Patient/).first()).toBeVisible() // FHIR type
    await expect(row.getByRole("heading", { level: 3 })).toBeVisible()
    scores.push(Number((await row.getByText(/^\d+%/).innerText()).match(/\d+/)![0]))
  }
  expect(scores).toEqual([...scores].sort((a, b) => b - a))

  const lab = rows.filter({ hasText: "Lab report" }).first()
  await expect(lab).toContainText("Complete blood count")
  await expect(lab).toContainText("MRN") // patient name and MRN on the card
  await expect(lab.locator("mark").first()).toBeVisible() // query words highlighted
  await expect(page.getByRole("status")).toHaveText(/\d+ results? for abnormal blood glucose/)
  await page.screenshot({ path: `${SHOTS}/results-light.png`, fullPage: true })
})

test("each patient's AI summary appears once: as its own result or as a snippet", async ({ page }) => {
  await search(page, "abnormal blood glucose")
  const rows = await results(page).all()
  const summarized = new Set<string>()
  for (const row of rows) {
    if ((await row.getAttribute("data-kind")) === "summary") summarized.add((await row.getAttribute("data-patient"))!)
  }
  let snippets = 0
  for (const row of rows) {
    const patient = (await row.getAttribute("data-patient"))!
    const notes = await row.getByRole("note", { name: "AI summary" }).count()
    if ((await row.getAttribute("data-kind")) === "summary" || summarized.has(patient)) expect(notes).toBe(0)
    snippets += notes
  }
  expect(summarized.size).toBeGreaterThan(0) // summaries are searchable records
  expect(snippets).toBeGreaterThan(0) // and patients without one in the list still get the snippet
})

test("example queries run a search", async ({ page }) => {
  await page.getByRole("button", { name: "bone scan for prostate cancer" }).click()
  await expect(searchBox(page)).toHaveValue("bone scan for prostate cancer")
  await expect(results(page).first()).toContainText("Bone Scan")
})

test("keyboard: slash focuses search, arrows move, enter opens, escape closes", async ({ page }) => {
  await page.locator("body").click({ position: { x: 5, y: 300 } })
  await page.keyboard.press("/")
  await expect(searchBox(page)).toBeFocused()
  await search(page, "headache with visual aura")

  await page.keyboard.press("ArrowDown")
  const first = page.locator("[data-result-index='0']")
  await expect(first).toBeFocused()
  await page.keyboard.press("ArrowDown")
  await expect(page.locator("[data-result-index='1']")).toBeFocused()
  await page.keyboard.press("ArrowUp")
  await expect(first).toBeFocused()

  await page.keyboard.press("Enter")
  const drawer = page.getByRole("dialog")
  await expect(drawer).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(drawer).toBeHidden()

  await searchBox(page).focus()
  await page.keyboard.press("Escape")
  await expect(searchBox(page)).toHaveValue("")
})

test("patient drawer shows the banner, AI summary and linked FHIR resources", async ({ page }) => {
  await search(page, "abnormal blood glucose")
  await results(page).filter({ hasText: "Complete blood count" }).first().getByRole("button").click()

  const drawer = page.getByRole("dialog")
  await expect(drawer).toBeVisible()
  await expect(drawer.getByText("MRN", { exact: true })).toBeVisible()
  await expect(drawer.getByText("Patient ID", { exact: true })).toBeVisible()
  await expect(drawer.getByRole("heading", { name: "AI summary" })).toBeVisible()
  await expect(drawer.getByText(/not a clinical decision/)).toBeVisible()
  await expect(drawer.getByText(/automatic checks passed/)).toBeVisible()
  await expect(drawer.getByRole("heading", { name: /Linked FHIR resources/ })).toBeVisible()
  await expect(drawer.getByRole("heading", { name: /Lab results/ })).toBeVisible()

  // The clicked report starts open and shows its values.
  const report = drawer.locator("li[id^='rec-hapi-report-']").first()
  await expect(report.getByRole("button")).toHaveAttribute("aria-expanded", "true")
  await expect(report).toContainText("Glucose")

  // A lab result opens on demand.
  const observation = drawer.locator("li[id^='rec-hapi-obs-']").first()
  await observation.getByRole("button").click()
  await expect(observation).toContainText(/mmol\/L|cells/)
  await page.screenshot({ path: `${SHOTS}/drawer-light.png` })

  await drawer.getByRole("button", { name: "Close" }).click()
  await expect(drawer).toBeHidden()
})

test("citations in the AI summary open the cited record", async ({ page }) => {
  await search(page, "heart failure with a leg wound")
  await results(page).first().getByRole("button").click()
  const drawer = page.getByRole("dialog")
  const citation = drawer.locator("section[aria-labelledby='summary-heading'] button").first()
  await expect(citation).toBeVisible()
  await citation.click()
  await expect(drawer.locator("li[id^='rec-'] button[aria-expanded='true']").first()).toBeVisible()
})

test("resource type filter limits results to that FHIR type", async ({ page }) => {
  await page.getByRole("combobox", { name: "Record type" }).click()
  await page.getByRole("option", { name: "Lab and imaging reports" }).click()
  await search(page, "abnormal result")
  for (const row of await results(page).all()) {
    await expect(row).toContainText("DiagnosticReport")
  }
  await expect(page.getByRole("button", { name: "Clear filters" })).toBeVisible()
})

test("date range filter, empty result and clearing filters", async ({ page }) => {
  await search(page, "wound care")
  await page.getByRole("button", { name: /Date range/ }).click()
  await page.getByRole("combobox", { name: /month/i }).selectOption({ label: "Jan" })
  await page.getByRole("combobox", { name: /year/i }).selectOption("1990")
  await page.getByRole("button", { name: /January 1st, 1990/ }).click()
  await page.getByRole("button", { name: /January 2nd, 1990/ }).click()
  await page.keyboard.press("Escape")
  await expect(page.getByRole("button", { name: /Date range: 1 Jan 1990 to 2 Jan 1990/ })).toBeVisible()

  await expect(page.getByRole("heading", { name: 'No records match "wound care"' })).toBeVisible()
  await page.getByRole("button", { name: "Clear filters" }).first().click()
  await expect(results(page).first()).toBeVisible()
  await expect(page.getByRole("button", { name: "Date range: Any date" })).toBeVisible()
})

test("shows a clear error when the search service is down", async ({ page, consoleErrors }) => {
  await page.route("**/search**", (route) => route.abort())
  await searchBox(page).fill("chest pain")
  await expect(page.getByText("Search is unavailable")).toBeVisible()
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible()
  consoleErrors.length = 0 // the aborted requests are expected here
})

test("dark theme toggles and is remembered", async ({ page }) => {
  await page.getByRole("button", { name: "Switch to dark theme" }).click()
  await expect(page.locator("html")).toHaveClass(/dark/)
  await page.reload()
  await expect(page.locator("html")).toHaveClass(/dark/)
  await expect(page.getByRole("button", { name: "Switch to light theme" })).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/start-dark.png` })

  await search(page, "abnormal blood glucose")
  await page.screenshot({ path: `${SHOTS}/results-dark.png`, fullPage: true })
  await results(page).filter({ hasText: "Complete blood count" }).first().getByRole("button").click()
  await expect(page.getByRole("dialog")).toBeVisible()
  await page.screenshot({ path: `${SHOTS}/drawer-dark.png` })
})

test("phone width: no sideways scrolling and a full-width drawer", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 })
  await search(page, "abnormal blood glucose")
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await page.screenshot({ path: `${SHOTS}/results-mobile.png`, fullPage: true })

  await results(page).first().getByRole("button").click()
  const box = await page.getByRole("dialog").boundingBox()
  expect(Math.round(box!.width)).toBe(375)
  await page.screenshot({ path: `${SHOTS}/drawer-mobile.png` })
})

test("no serious accessibility violations in light and dark", async ({ page }) => {
  await scan(page, "start, light")
  await search(page, "abnormal blood glucose")
  await scan(page, "results, light")
  await results(page).filter({ hasText: "Complete blood count" }).first().getByRole("button").click()
  await expect(page.getByRole("dialog")).toBeVisible()
  await scan(page, "drawer, light")
  await page.keyboard.press("Escape")

  await page.getByRole("button", { name: "Switch to dark theme" }).click()
  await scan(page, "results, dark")
  await results(page).filter({ hasText: "Complete blood count" }).first().getByRole("button").click()
  await expect(page.getByRole("dialog")).toBeVisible()
  await scan(page, "drawer, dark")
})

for (const width of [320, 375, 768, 1024, 1440, 1920]) {
  test(`layout holds at ${width}px wide`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 768 ? 800 : 900 })
    await search(page, "abnormal blood glucose")
    const sideways = () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(await sideways()).toBeLessThanOrEqual(0)
    await expect(results(page).first()).toBeVisible()
    await page.screenshot({ path: `${SHOTS}/layout-${width}.png`, fullPage: true })

    // Date picker fits on screen
    await page.getByRole("button", { name: /Date range/ }).click()
    const picker = page.getByRole("dialog")
    await expect(picker).toBeVisible()
    const box = (await picker.boundingBox())!
    expect(box.x).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width).toBeLessThanOrEqual(width + 0.5) // sub-pixel rounding when flush to the edge
    await page.keyboard.press("Escape")

    // Drawer: full width on phones, a side panel on larger screens, never wider than the screen
    await results(page).first().getByRole("button").click()
    const drawer = page.getByRole("dialog")
    await expect(drawer).toBeVisible()
    const d = (await drawer.boundingBox())!
    expect(d.width).toBeLessThanOrEqual(width + 0.5)
    if (width < 640) expect(Math.round(d.width)).toBe(width)
    else expect(d.width).toBeLessThan(width)
    await page.evaluate(() => Promise.all(document.getAnimations().filter((a) => a.effect?.getComputedTiming().iterations !== Infinity).map((a) => a.finished.catch(() => null))))
    await expect(drawer.getByRole("heading", { name: "AI summary" })).toBeVisible()
    const drawerSideways = await drawer.evaluate((el) => el.scrollWidth - el.clientWidth)
    expect(drawerSideways).toBeLessThanOrEqual(0) // nothing inside the drawer sticks out
    await page.screenshot({ path: `${SHOTS}/drawer-${width}.png` })
  })
}
