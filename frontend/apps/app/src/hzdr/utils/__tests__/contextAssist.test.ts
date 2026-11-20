import { afterEach, describe, expect, it } from 'vitest'

import type { ColumnSuggestion } from '../../types'
import {
  datasetSuggestions,
  isAlreadyInContext,
  loadIgnoredSuggestions,
  saveIgnoredSuggestions,
  suggestionFormState,
} from '../contextAssist'
import { buildHdf5DatasetOptions } from '../hdf5'

const suggestion = (
  overrides: Partial<ColumnSuggestion> = {}
): ColumnSuggestion => ({
  id: 'labfrog:ICT charge',
  title: 'ICT charge (nC)',
  function: 'ict_charge',
  origin: 'labfrog',
  originLabel: 'LabFrog record',
  field: 'labfrog_variables.ICT charge.value',
  path: 'meta#labfrog_variables.ICT charge.value',
  type: 'number',
  unit: 'nC',
  count: 9,
  total: 10,
  examples: ['1.5'],
  reason: 'Recorded on 9 of 10 shots',
  starter: true,
  block: '',
  display: 'number',
  displayLabel: 'Number column',
  displayHelp: '',
  ...overrides,
})

afterEach(() => window.localStorage.clear())

describe('datasetSuggestions', () => {
  it('offers images, lineouts and scalars with the matching preview', () => {
    const options = buildHdf5DatasetOptions(
      [
        { name: 'cam/image', shape: [64, 64], dtype: 'uint16' },
        { name: 'spectrometer/lineout', shape: [512], dtype: 'float64' },
        { name: 'ict/charge', shape: [], dtype: 'float64' },
        { name: 'notes', shape: [], dtype: 'string' },
      ],
      'shot'
    )
    const suggestions = datasetSuggestions(options, 'shot 3')
    expect(suggestions.map((entry) => [entry.title, entry.display])).toEqual([
      ['Image', 'image-preview'],
      ['Lineout', 'lineout-preview'],
      ['Charge', 'hdf5'],
    ])
    expect(suggestions[0].reason).toContain('shot 3')
    expect(suggestions[0].block).toContain('def image(')
    expect(suggestions[0].block).toContain('"cam/image"')
  })

  it('keeps function names unique when datasets share a name', () => {
    const options = buildHdf5DatasetOptions(
      [
        { name: 'a/signal', shape: [8], dtype: 'float64' },
        { name: 'b/signal', shape: [8], dtype: 'float64' },
      ],
      'shot'
    )
    expect(
      datasetSuggestions(options, 'x').map((entry) => entry.function)
    ).toEqual(['signal', 'signal_2'])
  })
})

describe('isAlreadyInContext', () => {
  it('matches the data the file reads, not a shared function name', () => {
    const entry = suggestion()
    expect(isAlreadyInContext(entry, '')).toBe(false)
    expect(
      isAlreadyInContext(
        entry,
        "def x(run, value: 'meta#labfrog_variables.ICT charge.value' = None):"
      )
    ).toBe(true)
    // a column that only shares the name reads other data
    expect(
      isAlreadyInContext(entry, 'def ict_charge(run, value: "meta#other"):')
    ).toBe(false)
    // a longer path that only starts with this one is a different field
    expect(
      isAlreadyInContext(
        entry,
        '"meta#labfrog_variables.ICT charge.value.extra"'
      )
    ).toBe(false)
  })
})

describe('suggestionFormState', () => {
  it('maps metadata and HDF5 suggestions onto builder inputs', () => {
    expect(suggestionFormState(suggestion())).toEqual({
      fieldKind: 'metadata',
      selectedInputs: ['metadata:labfrog_variables.ICT charge.value'],
      fieldTitle: 'ICT charge (nC)',
    })
    expect(
      suggestionFormState(
        suggestion({
          origin: 'hdf5',
          field: 'cam/image',
          display: 'image-preview',
          title: 'Image',
        })
      )
    ).toEqual({
      fieldKind: 'image-preview',
      selectedInputs: ['hdf5:cam/image'],
      fieldTitle: 'Image',
    })
  })
})

describe('ignored suggestions', () => {
  it('are remembered per campaign and survive bad storage', () => {
    saveIgnoredSuggestions('radbio', ['labfrog:a'])
    expect(loadIgnoredSuggestions('radbio')).toEqual(['labfrog:a'])
    expect(loadIgnoredSuggestions('other')).toEqual([])
    saveIgnoredSuggestions('radbio', [])
    expect(
      window.localStorage.getItem('hzdr.contextAssist.ignored.radbio')
    ).toBe(null)
    window.localStorage.setItem('hzdr.contextAssist.ignored.radbio', '{bad')
    expect(loadIgnoredSuggestions('radbio')).toEqual([])
  })
})
