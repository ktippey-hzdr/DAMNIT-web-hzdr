import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { MemoryRouter } from 'react-router'

import { CampaignCards } from '../CampaignCards'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('CampaignCards status strip', () => {
  it('shows available links and catalog producer status on one line', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        const value = url.endsWith('/scicat')
          ? {
              configured: true,
              registered: true,
              pid: 'pid-1',
              dataset_url: 'https://scicat.example/datasets/pid-1',
            }
          : url.endsWith('/wiki')
            ? {
                configured: true,
                page_title: 'FWKT:Radbio',
                page_url: 'https://wiki.example/radbio',
              }
            : {
                watchdog_hosts: [{ host: 'daq-1' }],
                shotcounter: { status: 'active', last_event_at: null },
              }
        return Promise.resolve({ ok: true, json: () => Promise.resolve(value) })
      })
    )
    render(
      <MantineProvider>
        <MemoryRouter>
          <CampaignCards sourceKey="radbio" />
        </MemoryRouter>
      </MantineProvider>
    )

    expect(await screen.findByRole('link', { name: 'pid-1' })).toHaveAttribute(
      'href',
      'https://scicat.example/datasets/pid-1'
    )
    expect(screen.getByRole('link', { name: 'FWKT:Radbio' })).toHaveAttribute(
      'href',
      'https://wiki.example/radbio'
    )
    expect(
      await screen.findByRole('link', { name: 'Shotcounter active, 1 watchdog' })
    ).toHaveAttribute('href', '/flow-monitor')
  })

  it('keeps the page usable when optional endpoints fail', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new Error('offline')))
    )
    render(
      <MantineProvider>
        <MemoryRouter>
          <CampaignCards sourceKey="radbio" />
        </MemoryRouter>
      </MantineProvider>
    )
    expect(await screen.findByText('no page linked')).toBeInTheDocument()
    expect(screen.getByText('not registered yet')).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Flow monitor' })
    ).toHaveAttribute('href', '/flow-monitor')
  })
})
