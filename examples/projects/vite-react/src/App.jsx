import { useState } from 'react'

export default function App() {
  const [count, setCount] = useState(0)

  return (
    <main>
      <p className="eyebrow">Vite + React example</p>
      <h1>A tiny frontend that is ready to change.</h1>
      <p>
        It includes one component, one stylesheet, and the usual development
        and production scripts.
      </p>
      <button type="button" onClick={() => setCount((value) => value + 1)}>
        Count is {count}
      </button>
    </main>
  )
}
