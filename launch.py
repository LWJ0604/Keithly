from keithley_lab.gui import main
import sys

if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        from keithley_lab.selfcheck import run
        sys.exit(run())
    elif sys.argv[1:]:
        raise SystemExit('Only --self-test is supported; measurements require local GUI preflight.')
    else:
        main()
