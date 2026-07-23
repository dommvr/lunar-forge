import { useEffect, useState } from 'react'

const checks = [
  'A heading with a stable selector',
  'Interactive button and form controls',
  'Enough content for a useful full-page screenshot',
]

export default function App() {
  const [count, setCount] = useState(0)
  const [name, setName] = useState('')
  const [message, setMessage] = useState('No form submission yet.')

  useEffect(() => {
    const search = new URLSearchParams(window.location.search)
    if (search.get('consoleError') === '1') {
      console.error('Browser demo requested an optional console error.')
    }
  }, [])

  function handleSubmit(event) {
    event.preventDefault()
    const displayName = name.trim() || 'browser tester'
    setMessage(`Hello, ${displayName}. The form is working.`)
  }

  return (
    <main>
      <section className="hero" aria-labelledby="main-heading">
        <div className="hero-copy">
          <p className="eyebrow">LunarForge browser validation</p>
          <h1 id="main-heading">A small page with plenty to inspect.</h1>
          <p className="lede">
            Use this deterministic React app to validate layout, interactions,
            console output, local requests, and full-page screenshots.
          </p>
        </div>

        <div className="demo-panel" aria-label="Interactive controls">
          <div className="counter-row">
            <button
              id="counter-button"
              type="button"
              onClick={() => setCount((current) => current + 1)}
            >
              Increase count
            </button>
            <output htmlFor="counter-button" aria-live="polite">
              Count: {count}
            </output>
          </div>

          <form onSubmit={handleSubmit}>
            <label htmlFor="demo-name">Name for the form check</label>
            <input
              id="demo-name"
              name="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Ada"
            />
            <button type="submit">Submit form</button>
          </form>
          <p id="form-status" className="form-status" aria-live="polite">
            {message}
          </p>
        </div>
      </section>

      <section className="below-fold" aria-labelledby="below-fold-heading">
        <div>
          <p className="eyebrow">Below the fold</p>
          <h2 id="below-fold-heading">Full-page capture reaches this section.</h2>
          <p>
            The first section fills the initial viewport. This second section
            makes a full-page screenshot visibly different from a viewport-only
            capture without loading remote assets.
          </p>
        </div>
        <ul>
          {checks.map((check) => (
            <li key={check}>{check}</li>
          ))}
        </ul>
      </section>
    </main>
  )
}
