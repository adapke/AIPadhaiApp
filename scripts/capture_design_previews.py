"""Capture design previews of each AI Pathshala module from the live
local server via headless Chromium."""

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

SHOTS = [
    # (module key, label, viewport_w, viewport_h, full_page, extra_setup)
    ("create",    "01_create_lesson_desktop",   1280, 900,  True,  None),
    ("library",   "02_my_library_desktop",      1280, 900,  True,  None),
    ("flashcards","03_flashcards_module",       1280, 900,  True,  "flashcards"),
    ("curriculum","04_curriculum_mapper",       1280, 900,  True,  "curriculum"),
    ("path",      "05_learning_path",           1280, 900,  True,  None),
    ("teacher",   "06_teacher_studio",          1280, 900,  True,  None),
    ("parent",    "07_parent_dashboard",        1280, 900,  True,  "parent"),
    ("voice",     "08_voice_tutor_stub",        1280, 900,  True,  None),
    ("school",    "09_school_admin_stub",       1280, 900,  True,  None),
    ("create",    "10_mobile_create_lesson",     390, 844,  True,  None),
    ("flashcards","11_mobile_flashcards",        390, 844,  True,  "flashcards"),
]


async def main():
    out_dir = Path("design_previews")
    out_dir.mkdir(exist_ok=True)

    async with async_playwright() as p:
        # Use the older full chromium build (the headless-shell version
        # Playwright wants by default isn't installed in this sandbox)
        browser = await p.chromium.launch(
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        for module, label, w, h, full, extra in SHOTS:
            ctx = await browser.new_context(viewport={"width": w, "height": h},
                                             device_scale_factor=2)
            page = await ctx.new_page()
            await page.goto("http://127.0.0.1:8765/", wait_until="networkidle")

            # Switch to the named module via the sidebar's data-module button
            await page.evaluate(f"document.querySelector('.nav-item[data-module=\"{module}\"]')?.click()")
            await page.wait_for_timeout(400)

            # For the auth modal, trigger it on one of the shots so it
            # appears in at least one preview (but only if the next item
            # specifically requests it). Keep this simple — no auth shot
            # here unless we want it.

            # If the module needs data, simulate it via fetched stats
            if extra == "parent":
                # Trigger lazy-load
                await page.evaluate("typeof pdLoad === 'function' && pdLoad()")
                await page.wait_for_timeout(800)
            elif extra == "curriculum":
                await page.evaluate("typeof cmLoadIndex === 'function' && cmLoadIndex()")
                await page.wait_for_timeout(800)
            elif extra == "flashcards":
                # Populate a fake deck so the deck UI shows up in the preview
                await page.evaluate("""() => {
                  if (typeof fcDeck !== 'undefined') {
                    fcDeck = [
                      {front: "What is photosynthesis?",
                       back: "The process by which plants make food using sunlight, water, and CO₂. Happens in the chloroplasts of green leaves.",
                       hint: "Photo = light, synthesis = put together",
                       tags: ["biology","photosynthesis","plants"],
                       srs: {ease: 2.5, interval: 0, due: Date.now()}},
                      {front: "What gas do plants release?",
                       back: "Oxygen (O₂) — released as a by-product of photosynthesis.",
                       hint: null,
                       tags: ["biology","gases"],
                       srs: {ease: 2.5, interval: 1, due: Date.now()}},
                      {front: "Where does photosynthesis happen?",
                       back: "In the chloroplasts, mainly in the leaves of green plants.",
                       hint: null,
                       tags: ["biology","cells"],
                       srs: {ease: 2.5, interval: 3, due: Date.now()}},
                    ];
                    fcIndex = 0;
                    fcLessonId = 'demo-photosynthesis-lesson-1234';
                    fcRender();
                  }
                }""")
                await page.wait_for_timeout(500)

            out = out_dir / f"{label}.png"
            await page.screenshot(path=out, full_page=full)
            print(f"  {out} ({out.stat().st_size:,} bytes)")
            await ctx.close()

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
