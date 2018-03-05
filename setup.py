from setuptools import setup, find_packages

setup(
        name='ecobee-exporter',
        version='1.0.0',
        description='Ecobee Exporter',

        author='Richard Burnison',
        author_email='richard@burnison.ca',
        url='https://github.com/burnison/ecobee-exporter',

        packages=find_packages(),
        include_package_data=True,
        zip_safe=False,

        install_requires=[
            'oauth2client>=4.1.0',
            'graphitesend>=0.10.0',
        ],

        entry_points={
            'console_scripts':['ecobee-exporter = ecobee.exporter:main']
        }
)
